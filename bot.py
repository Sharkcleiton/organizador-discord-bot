import os
import asyncio
import logging

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
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("organizador")

TOKEN = os.environ["DISCORD_TOKEN"]

CANAL_ASSISTENTE = "assistente"
CANAL_TAREFAS = "tarefas"
CANAL_LEMBRETES = "lembretes"
CANAL_METAS = "metas"

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


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    if message.channel.name != CANAL_ASSISTENTE:
        return

    pendente = pendentes.get(message.channel.id)
    resultado = await processar_mensagem(message.content, pendente=pendente)
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
            await message.channel.send(f"\U0001F4CC Anotei em #{CANAL_TAREFAS}: {linha[4:]}")

        elif tipo == "lembrete":
            destino_lembretes = canal(message.guild, CANAL_LEMBRETES)
            canal_id = str(destino_lembretes.id) if destino_lembretes else str(message.channel.id)
            criar_lembrete(acao["titulo"], acao["disparar_em"], canal_id)
            await message.channel.send(
                f"⏰ Combinado! Vou te lembrar em **{acao['disparar_em']}**: {acao['titulo']}"
            )

        elif tipo == "meta":
            criar_meta(
                acao["titulo"], acao.get("descricao"), acao.get("tipo_meta", "mensal"), acao.get("prazo")
            )
            destino = canal(message.guild, CANAL_METAS)
            if destino:
                await destino.send(f"\U0001F3AF **{acao['titulo']}**")
            await message.channel.send(f"\U0001F3AF Meta registrada em #{CANAL_METAS}: **{acao['titulo']}**")

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
                    await canal_obj.send(f"⏰ **LEMBRETE**\n{lembrete['titulo']}")
                marcar_lembrete_enviado(lembrete["id"])
        except Exception:
            log.exception("erro no loop de lembretes")
        await asyncio.sleep(60)


if __name__ == "__main__":
    client.run(TOKEN)
