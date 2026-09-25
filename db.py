import os
from datetime import datetime, timezone

from supabase import create_client

_client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])


def criar_tarefa(titulo: str, descricao: str | None = None, prazo: str | None = None):
    _client.table("tarefas").insert({
        "titulo": titulo,
        "descricao": descricao,
        "prazo": prazo,
    }).execute()


def criar_lembrete(titulo: str, disparar_em: str, discord_channel_id: str, discord_user_id: str):
    _client.table("lembretes").insert({
        "titulo": titulo,
        "disparar_em": disparar_em,
        "discord_channel_id": discord_channel_id,
        "discord_user_id": discord_user_id,
    }).execute()


def criar_meta(titulo: str, descricao: str | None, tipo_meta: str, prazo: str | None):
    _client.table("metas").insert({
        "titulo": titulo,
        "descricao": descricao,
        "tipo": tipo_meta,
        "prazo": prazo,
    }).execute()


def lembretes_pendentes() -> list[dict]:
    agora = datetime.now(timezone.utc).isoformat()
    resposta = (
        _client.table("lembretes")
        .select("*")
        .eq("enviado", False)
        .lte("disparar_em", agora)
        .execute()
    )
    return resposta.data


def marcar_lembrete_enviado(lembrete_id: str):
    _client.table("lembretes").update({"enviado": True}).eq("id", lembrete_id).execute()
