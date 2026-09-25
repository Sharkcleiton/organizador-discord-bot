import os
import asyncio
import logging
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

import discord

from assistente import processar_mensagem
from db import (
    criar_tarefa,
    criar_lembrete,
    criar_meta,
    lembretes_pendentes,
    marcar_lembrete_enviado,
    criar_lembrete_recorrente,
    lembretes_recorrentes_ativos,
    marcar_recorrente_executado,
    criar_execucao_habito,
    execucoes_para_cobrar,
    marcar_execucao_cobrada,
    marcar_execucao_status,
    criar_conta,
    contas_a_vencer,
    marcar_conta_lembrada,
    criar_gasto,
    total_gastos_mes,
    definir_limite_financeiro,
    obter_config_financeiro,
    marcar_avisado_hoje,
    contar_tarefas,
    contar_metas,
    contar_habitos_recentes,
    contar_lembretes_pendentes,
    contas_pendentes_resumo,
    buscar_por_titulo,
    excluir_por_id,
)
import calendar

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("organizador")

TOKEN = os.environ["DISCORD_TOKEN"]

CANAL_ASSISTENTE = "assistente"
CANAL_TAREFAS = "tarefas"
CANAL_LEMBRETES = "lembretes"
CANAL_METAS = "metas"
CANAL_CONTAS = "contas"
CANAL_GERAL = "geral"

DIAS_ANTES_DE_LEMBRAR_CONTA = 3

AVISOS_GASTO = [
    "tá gastando muito, patrão... daqui a pouco tá tudo lascado, viu 😅",
    "chefe, olha o ritmo dos gastos esse mês, hein — não vacila",
    "opa, vou avisando: gasto tá pesado esse mês, chefe. Segura a onda.",
]

FUSO = ZoneInfo(os.environ.get("FUSO_HORARIO", "America/Sao_Paulo"))

NOME_CATEGORIA = {
    "tarefa": "tarefa",
    "conta": "conta",
    "lembrete": "lembrete",
    "meta": "meta",
    "lembrete_recorrente": "hábito recorrente",
}
CATEGORIAS_BUSCA = list(NOME_CATEGORIA.keys())

DIAS_NOMES = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

HORAS_ANTES_DE_COBRAR = 3

COBRANCAS = [
    'ei chefe, você foi fazer "{titulo}" hoje? Cadê, hein?',
    'cadê você, chefe? Combinamos "{titulo}" hoje e não vi confirmação. Qual foi a desculpa dessa vez?',
    'opa chefe, passando aqui: "{titulo}" rolou hoje ou não? Tempo, preguiça, ou o quê?',
]

SUGESTOES_PAUSA = [
    "levanta e dá uma alongada rápida",
    "bebe um copo d'água",
    "descansa os olhos da tela por 2 minutinhos",
    "dá uma caminhada curta lá fora",
    "respira fundo umas 3 vezes antes de voltar",
]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

pendentes: dict[int, dict] = {}  # channel_id -> contexto da pergunta em aberto

COOLDOWN_CHEFE = timedelta(minutes=20)
ultimo_chefe: dict[int, datetime] = {}  # channel_id -> quando foi a última vez que disse "chefe"


def canal(guild: discord.Guild, nome: str) -> discord.TextChannel | None:
    return discord.utils.get(guild.text_channels, name=nome)


def deve_dizer_chefe(channel_id: int) -> bool:
    agora = datetime.now(FUSO)
    ultimo = ultimo_chefe.get(channel_id)
    if ultimo is None or (agora - ultimo) > COOLDOWN_CHEFE:
        ultimo_chefe[channel_id] = agora
        return True
    return False


@client.event
async def on_ready():
    log.info("Conectado como %s", client.user)
    for guild in client.guilds:
        if canal(guild, CANAL_CONTAS) is None:
            try:
                await guild.create_text_channel(CANAL_CONTAS)
                log.info("Canal #%s criado em %s", CANAL_CONTAS, guild.name)
            except discord.Forbidden:
                log.warning("Sem permissão pra criar #%s em %s", CANAL_CONTAS, guild.name)
    client.loop.create_task(loop_lembretes())
    client.loop.create_task(loop_pausas())
    client.loop.create_task(loop_recorrentes())
    client.loop.create_task(loop_cobranca())
    client.loop.create_task(loop_contas())
    client.loop.create_task(loop_financeiro())


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    if message.channel.name != CANAL_ASSISTENTE:
        return

    pendente = pendentes.get(message.channel.id)
    usar_chefe = deve_dizer_chefe(message.channel.id)
    saud = ", chefe" if usar_chefe else ""
    try:
        resultado = await processar_mensagem(message.content, pendente=pendente, usar_chefe=usar_chefe)
    except Exception:
        log.exception("erro ao processar mensagem")
        await message.channel.send("⚠️ Deu um erro aqui do meu lado processando isso — tenta de novo em instantes.")
        return
    pendentes.pop(message.channel.id, None)

    for acao in resultado.get("acoes", []):
        tipo = acao.get("tipo")

        if tipo == "tarefa":
            criar_tarefa(acao["titulo"], acao.get("descricao"), acao.get("prazo"))
            linha = f"\U0001F4CC **{acao['titulo']}**"
            if acao.get("prazo"):
                linha += f" — prazo {acao['prazo']}"
            destino = canal(message.guild, CANAL_TAREFAS)
            if destino:
                await destino.send(linha)
            await message.channel.send(f"\U0001F4CC Anotado{saud}! (em #{CANAL_TAREFAS}) {linha[4:]}")

        elif tipo == "lembrete":
            destino_lembretes = canal(message.guild, CANAL_LEMBRETES)
            canal_id = str(destino_lembretes.id) if destino_lembretes else str(message.channel.id)
            criar_lembrete(acao["titulo"], acao["disparar_em"], canal_id, str(message.author.id))
            horario_fmt = datetime.fromisoformat(acao["disparar_em"]).strftime("%d/%m às %H:%M")
            await message.channel.send(
                f"⏰ Combinado{saud}! Vou te lembrar **{horario_fmt}**: {acao['titulo']}"
            )

        elif tipo == "meta":
            criar_meta(
                acao["titulo"], acao.get("descricao"), acao.get("tipo_meta", "mensal"), acao.get("prazo")
            )
            destino = canal(message.guild, CANAL_METAS)
            if destino:
                await destino.send(f"\U0001F3AF **{acao['titulo']}**")
            await message.channel.send(f"\U0001F3AF Meta registrada{saud}! (em #{CANAL_METAS}) **{acao['titulo']}**")

        elif tipo == "lembrete_recorrente":
            destino_lembretes = canal(message.guild, CANAL_LEMBRETES)
            canal_id = str(destino_lembretes.id) if destino_lembretes else str(message.channel.id)
            criar_lembrete_recorrente(
                acao["titulo"], acao["dias_semana"], acao["horario"], canal_id, str(message.author.id)
            )
            dias_fmt = ", ".join(DIAS_NOMES[d] for d in sorted(acao["dias_semana"]))
            await message.channel.send(
                f"🔁 Combinado{saud}! Vou te lembrar de **{acao['titulo']}** toda(o) {dias_fmt} às {acao['horario']}"
            )

        elif tipo == "checkin_habito":
            if pendente and pendente.get("execucao_id"):
                status = "feito" if acao.get("feito") else "nao_feito"
                marcar_execucao_status(pendente["execucao_id"], status)
            await message.channel.send(acao.get("resposta_chefe", "Beleza."))

        elif tipo == "conta":
            destino_contas = canal(message.guild, CANAL_CONTAS)
            canal_id = str(destino_contas.id) if destino_contas else str(message.channel.id)
            criar_conta(acao["titulo"], acao["valor"], acao["vencimento"], canal_id, str(message.author.id))
            linha = f"\U0001F4B0 **{acao['titulo']}** — R$ {acao['valor']:.2f}, vence {acao['vencimento']}"
            if destino_contas:
                await destino_contas.send(linha)
            await message.channel.send(f"\U0001F4B0 Anotado{saud}! (em #{CANAL_CONTAS}) {linha[4:]}")

        elif tipo == "gasto":
            criar_gasto(acao["descricao"], acao["valor"], acao.get("categoria"))
            destino_contas = canal(message.guild, CANAL_CONTAS)
            if destino_contas:
                await destino_contas.send(f"\U0001F4B8 {acao['descricao']} — R$ {acao['valor']:.2f}")
            await message.channel.send(
                f"\U0001F4B8 Registrado{saud}: {acao['descricao']} — R$ {acao['valor']:.2f}"
            )
            await checar_ritmo_gastos(message.channel, message.author)

        elif tipo == "limite_financeiro":
            definir_limite_financeiro(acao["valor"], str(message.channel.id), str(message.author.id))
            await message.channel.send(f"\U0001F4CA Beleza{saud} — limite mensal definido em R$ {acao['valor']:.2f}")

        elif tipo == "painel":
            embed = montar_painel()
            destino_geral = canal(message.guild, CANAL_GERAL)
            if destino_geral:
                await destino_geral.send(embed=embed)
                await message.channel.send(f"\U0001F4CA Beleza{saud}! Painel atualizado em #{CANAL_GERAL}")
            else:
                await message.channel.send(embed=embed)

        elif tipo == "excluir":
            categoria = acao.get("categoria")
            termo = acao["termo"]
            categorias = [categoria] if categoria in NOME_CATEGORIA else CATEGORIAS_BUSCA

            encontrados = []  # (categoria, item)
            for cat in categorias:
                for item in buscar_por_titulo(cat, termo):
                    encontrados.append((cat, item))

            if not encontrados:
                await message.channel.send(f"❌ Não achei nada com \"{termo}\"{saud}.")
            elif len(encontrados) == 1:
                cat, item = encontrados[0]
                view = ConfirmarExclusao(cat, item["id"], item["titulo"], message.author.id)
                await message.channel.send(
                    f"Confirma excluir **{item['titulo']}** ({NOME_CATEGORIA[cat]})?", view=view
                )
            else:
                if categoria in NOME_CATEGORIA:
                    view = EscolherItemParaExcluir(categoria, [i for _, i in encontrados], message.author.id)
                    await message.channel.send(f"Achei mais de um com \"{termo}\" — qual deles?", view=view)
                else:
                    # mistura categorias: monta view manual com callbacks fixando a categoria certa por item
                    view = discord.ui.View(timeout=60)
                    for cat, item in encontrados[:5]:
                        botao = discord.ui.Button(label=f"[{NOME_CATEGORIA[cat]}] {item['titulo'][:60]}", style=discord.ButtonStyle.primary)

                        async def callback(interaction: discord.Interaction, cat=cat, item=item):
                            if interaction.user.id != message.author.id:
                                return
                            confirmar = ConfirmarExclusao(cat, item["id"], item["titulo"], message.author.id)
                            await interaction.response.edit_message(
                                content=f"Confirma excluir **{item['titulo']}** ({NOME_CATEGORIA[cat]})?", view=confirmar
                            )

                        botao.callback = callback
                        view.add_item(botao)
                    await message.channel.send(f"Achei mais de um com \"{termo}\" — qual deles?", view=view)

        elif tipo == "pergunta":
            pendentes[message.channel.id] = acao.get("contexto", {})
            await message.channel.send(f"❓ {acao['pergunta']}")

        elif tipo == "resposta":
            await message.channel.send(acao["texto"])


def montar_painel() -> discord.Embed:
    pendentes_t, concluidas_t = contar_tarefas()
    ativas_m, concluidas_m = contar_metas()
    feitos_h, perdidos_h = contar_habitos_recentes(30)
    n_lembretes = contar_lembretes_pendentes()
    n_contas, total_contas = contas_pendentes_resumo()

    agora_local = datetime.now(FUSO)
    total_gasto = total_gastos_mes(agora_local.year, agora_local.month)
    config = obter_config_financeiro()
    limite = float(config["limite_mensal"]) if config and config.get("limite_mensal") else None

    embed = discord.Embed(
        title="\U0001F4CA Seu desempenho",
        color=discord.Color.blurple(),
        timestamp=agora_local,
    )
    embed.add_field(name="\U0001F4CC Tarefas", value=f"{pendentes_t} pendentes · {concluidas_t} concluídas", inline=True)
    embed.add_field(name="\U0001F3AF Metas", value=f"{ativas_m} ativas · {concluidas_m} concluídas", inline=True)
    embed.add_field(name="⏰ Lembretes", value=f"{n_lembretes} pendentes", inline=True)
    embed.add_field(
        name="\U0001F501 Hábitos (30 dias)", value=f"{feitos_h} cumpridos · {perdidos_h} perdidos", inline=True
    )

    if n_contas or total_contas:
        embed.add_field(name="\U0001F4B0 Contas a pagar", value=f"{n_contas} conta(s) · R$ {total_contas:.2f}", inline=True)
    else:
        embed.add_field(name="\U0001F4B0 Contas a pagar", value="Nenhuma pendente", inline=True)

    if limite:
        resta = limite - total_gasto
        embed.add_field(
            name="\U0001F4B8 Gastos do mês",
            value=f"R$ {total_gasto:.2f} de R$ {limite:.2f} · ainda tem R$ {resta:.2f}",
            inline=True,
        )
    else:
        embed.add_field(name="\U0001F4B8 Gastos do mês", value=f"R$ {total_gasto:.2f} (sem limite definido)", inline=True)

    return embed


class ConfirmarExclusao(discord.ui.View):
    def __init__(self, categoria: str, item_id: str, titulo: str, autor_id: int):
        super().__init__(timeout=60)
        self.categoria = categoria
        self.item_id = item_id
        self.titulo = titulo
        self.autor_id = autor_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.autor_id

    @discord.ui.button(label="Excluir", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def excluir(self, interaction: discord.Interaction, botao: discord.ui.Button):
        excluir_por_id(self.categoria, self.item_id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"🗑️ Excluído: **{self.titulo}** ({NOME_CATEGORIA[self.categoria]})", view=self
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, botao: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"Beleza, deixei **{self.titulo}** como está.", view=self)


class EscolherItemParaExcluir(discord.ui.View):
    def __init__(self, categoria: str, itens: list[dict], autor_id: int):
        super().__init__(timeout=60)
        self.autor_id = autor_id
        for item in itens[:5]:
            self.add_item(self._criar_botao(categoria, item))
        cancelar = discord.ui.Button(label="Cancelar", style=discord.ButtonStyle.secondary)
        cancelar.callback = self._cancelar
        self.add_item(cancelar)

    def _criar_botao(self, categoria: str, item: dict) -> discord.ui.Button:
        rotulo = item["titulo"][:75]
        botao = discord.ui.Button(label=rotulo, style=discord.ButtonStyle.primary)

        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.autor_id:
                return
            view = ConfirmarExclusao(categoria, item["id"], item["titulo"], self.autor_id)
            await interaction.response.edit_message(
                content=f"Confirma excluir **{item['titulo']}** ({NOME_CATEGORIA[categoria]})?", view=view
            )

        botao.callback = callback
        return botao

    async def _cancelar(self, interaction: discord.Interaction):
        if interaction.user.id != self.autor_id:
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Beleza, não mexi em nada.", view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.autor_id


async def checar_ritmo_gastos(channel: discord.TextChannel, autor: discord.Member):
    config = obter_config_financeiro()
    if not config or not config.get("limite_mensal"):
        return
    agora_local = datetime.now(FUSO)
    hoje_str = agora_local.date().isoformat()
    if config.get("avisado_em") == hoje_str:
        return

    dias_no_mes = calendar.monthrange(agora_local.year, agora_local.month)[1]
    pct_tempo = agora_local.day / dias_no_mes
    total = total_gastos_mes(agora_local.year, agora_local.month)
    limite = float(config["limite_mensal"])
    pct_gasto = total / limite if limite else 0

    if pct_gasto > pct_tempo + 0.15:
        aviso = random.choice(AVISOS_GASTO)
        await channel.send(
            f"{autor.mention} {aviso} (já gastou R$ {total:.2f} de R$ {limite:.2f} esse mês)"
        )
        marcar_avisado_hoje(hoje_str)


async def loop_lembretes():
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            for lembrete in lembretes_pendentes():
                canal_obj = client.get_channel(int(lembrete["discord_channel_id"]))
                if canal_obj:
                    mencao = f"<@{lembrete['discord_user_id']}> " if lembrete.get("discord_user_id") else ""
                    await canal_obj.send(f"⏰ {mencao}**LEMBRETE, chefe**\n{lembrete['titulo']}")
                marcar_lembrete_enviado(lembrete["id"])
        except Exception:
            log.exception("erro no loop de lembretes")
        await asyncio.sleep(60)


async def loop_recorrentes():
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            agora_local = datetime.now(FUSO)
            dia_semana = agora_local.weekday()
            hora_str = agora_local.strftime("%H:%M")
            data_str = agora_local.date().isoformat()
            for rec in lembretes_recorrentes_ativos():
                if (
                    dia_semana in rec["dias_semana"]
                    and rec["horario"][:5] == hora_str
                    and rec.get("ultima_execucao") != data_str
                ):
                    canal_obj = client.get_channel(int(rec["discord_channel_id"]))
                    if canal_obj:
                        await canal_obj.send(
                            f"🔁 <@{rec['discord_user_id']}> **{rec['titulo']}** — hora de manter o hábito, chefe!"
                        )
                    criar_execucao_habito(rec["id"], data_str)
                    marcar_recorrente_executado(rec["id"], data_str)
        except Exception:
            log.exception("erro no loop de recorrentes")
        await asyncio.sleep(60)


async def loop_cobranca():
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(30 * 60)
        try:
            for execucao in execucoes_para_cobrar(HORAS_ANTES_DE_COBRAR):
                rec = execucao.get("lembretes_recorrentes") or {}
                canal_id = rec.get("discord_channel_id")
                canal_obj = client.get_channel(int(canal_id)) if canal_id else None
                if canal_obj:
                    titulo = rec.get("titulo", "seu hábito")
                    mencao = f"<@{rec['discord_user_id']}> " if rec.get("discord_user_id") else ""
                    cobranca = random.choice(COBRANCAS).format(titulo=titulo)
                    await canal_obj.send(f"{mencao}{cobranca}")
                    pendentes[canal_obj.id] = {
                        "tipo_pendente": "checkin_habito",
                        "execucao_id": execucao["id"],
                        "titulo": titulo,
                    }
                marcar_execucao_cobrada(execucao["id"])
        except Exception:
            log.exception("erro no loop de cobranca")


async def loop_contas():
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            for conta in contas_a_vencer(DIAS_ANTES_DE_LEMBRAR_CONTA):
                canal_obj = client.get_channel(int(conta["discord_channel_id"]))
                if canal_obj:
                    mencao = f"<@{conta['discord_user_id']}> " if conta.get("discord_user_id") else ""
                    await canal_obj.send(
                        f"\U0001F4B0 {mencao}**CONTA VENCENDO, chefe:** {conta['titulo']} — "
                        f"R$ {float(conta['valor']):.2f}, vence {conta['vencimento']}"
                    )
                marcar_conta_lembrada(conta["id"])
        except Exception:
            log.exception("erro no loop de contas")
        await asyncio.sleep(6 * 60 * 60)  # a cada 6 horas


async def loop_financeiro():
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(12 * 60 * 60)  # a cada 12 horas
        try:
            config = obter_config_financeiro()
            if not config or not config.get("limite_mensal") or not config.get("discord_channel_id"):
                continue
            canal_obj = client.get_channel(int(config["discord_channel_id"]))
            autor = await client.fetch_user(int(config["discord_user_id"])) if canal_obj else None
            if canal_obj and autor:
                await checar_ritmo_gastos(canal_obj, autor)
        except Exception:
            log.exception("erro no loop financeiro")


async def loop_pausas():
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(2 * 60 * 60)  # a cada 2 horas
        try:
            agora_local = datetime.now(FUSO)
            if 8 <= agora_local.hour < 22:
                sugestao = random.choice(SUGESTOES_PAUSA)
                for guild in client.guilds:
                    destino = canal(guild, CANAL_ASSISTENTE)
                    if destino and guild.owner:
                        await destino.send(f"💡 {guild.owner.mention} pausa rápida, chefe: {sugestao}")
        except Exception:
            log.exception("erro no loop de pausas")


if __name__ == "__main__":
    client.run(TOKEN)
