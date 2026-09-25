import os
import asyncio
import logging
import random
from datetime import datetime
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
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("organizador")

TOKEN = os.environ["DISCORD_TOKEN"]

CANAL_ASSISTENTE = "assistente"
CANAL_TAREFAS = "tarefas"
CANAL_LEMBRETES = "lembretes"
CANAL_METAS = "metas"

FUSO = ZoneInfo(os.environ.get("FUSO_HORARIO", "America/Sao_Paulo"))

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


def canal(guild: discord.Guild, nome: str) -> discord.TextChannel | None:
    return discord.utils.get(guild.text_channels, name=nome)


@client.event
async def on_ready():
    log.info("Conectado como %s", client.user)
    client.loop.create_task(loop_lembretes())
    client.loop.create_task(loop_pausas())
    client.loop.create_task(loop_recorrentes())
    client.loop.create_task(loop_cobranca())


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    if message.channel.name != CANAL_ASSISTENTE:
        return

    pendente = pendentes.get(message.channel.id)
    try:
        resultado = await processar_mensagem(message.content, pendente=pendente)
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
            await message.channel.send(f"\U0001F4CC Anotado, chefe! (em #{CANAL_TAREFAS}) {linha[4:]}")

        elif tipo == "lembrete":
            destino_lembretes = canal(message.guild, CANAL_LEMBRETES)
            canal_id = str(destino_lembretes.id) if destino_lembretes else str(message.channel.id)
            criar_lembrete(acao["titulo"], acao["disparar_em"], canal_id, str(message.author.id))
            horario_fmt = datetime.fromisoformat(acao["disparar_em"]).strftime("%d/%m às %H:%M")
            await message.channel.send(
                f"⏰ Combinado, chefe! Vou te lembrar **{horario_fmt}**: {acao['titulo']}"
            )

        elif tipo == "meta":
            criar_meta(
                acao["titulo"], acao.get("descricao"), acao.get("tipo_meta", "mensal"), acao.get("prazo")
            )
            destino = canal(message.guild, CANAL_METAS)
            if destino:
                await destino.send(f"\U0001F3AF **{acao['titulo']}**")
            await message.channel.send(f"\U0001F3AF Meta registrada, chefe! (em #{CANAL_METAS}) **{acao['titulo']}**")

        elif tipo == "lembrete_recorrente":
            destino_lembretes = canal(message.guild, CANAL_LEMBRETES)
            canal_id = str(destino_lembretes.id) if destino_lembretes else str(message.channel.id)
            criar_lembrete_recorrente(
                acao["titulo"], acao["dias_semana"], acao["horario"], canal_id, str(message.author.id)
            )
            dias_fmt = ", ".join(DIAS_NOMES[d] for d in sorted(acao["dias_semana"]))
            await message.channel.send(
                f"🔁 Combinado, chefe! Vou te lembrar de **{acao['titulo']}** toda(o) {dias_fmt} às {acao['horario']}"
            )

        elif tipo == "checkin_habito":
            if pendente and pendente.get("execucao_id"):
                status = "feito" if acao.get("feito") else "nao_feito"
                marcar_execucao_status(pendente["execucao_id"], status)
            await message.channel.send(acao.get("resposta_chefe", "Beleza, chefe."))

        elif tipo == "pergunta":
            pendentes[message.channel.id] = acao.get("contexto", {})
            await message.channel.send(f"❓ {acao['pergunta']}")

        elif tipo == "resposta":
            await message.channel.send(acao["texto"])


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
