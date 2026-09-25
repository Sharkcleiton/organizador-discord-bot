import os
from datetime import datetime, timedelta, timezone

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


def criar_lembrete_recorrente(
    titulo: str, dias_semana: list[int], horario: str, discord_channel_id: str, discord_user_id: str
):
    _client.table("lembretes_recorrentes").insert({
        "titulo": titulo,
        "dias_semana": dias_semana,
        "horario": horario,
        "discord_channel_id": discord_channel_id,
        "discord_user_id": discord_user_id,
    }).execute()


def lembretes_recorrentes_ativos() -> list[dict]:
    resposta = _client.table("lembretes_recorrentes").select("*").eq("ativo", True).execute()
    return resposta.data


def marcar_recorrente_executado(recorrente_id: str, data_str: str):
    _client.table("lembretes_recorrentes").update({"ultima_execucao": data_str}).eq("id", recorrente_id).execute()


def criar_execucao_habito(recorrente_id: str, data_str: str) -> str | None:
    resposta = (
        _client.table("execucoes_habito")
        .upsert({"recorrente_id": recorrente_id, "data": data_str}, on_conflict="recorrente_id,data")
        .execute()
    )
    return resposta.data[0]["id"] if resposta.data else None


def execucoes_para_cobrar(horas_limite: int) -> list[dict]:
    limite = (datetime.now(timezone.utc) - timedelta(hours=horas_limite)).isoformat()
    resposta = (
        _client.table("execucoes_habito")
        .select("*, lembretes_recorrentes(titulo,discord_channel_id,discord_user_id)")
        .eq("status", "pendente")
        .eq("cobrado", False)
        .lte("criado_em", limite)
        .execute()
    )
    return resposta.data


def marcar_execucao_cobrada(execucao_id: str):
    _client.table("execucoes_habito").update({"cobrado": True}).eq("id", execucao_id).execute()


def marcar_execucao_status(execucao_id: str, status: str):
    _client.table("execucoes_habito").update({"status": status}).eq("id", execucao_id).execute()


def criar_conta(titulo: str, valor: float, vencimento: str, discord_channel_id: str, discord_user_id: str):
    _client.table("contas").insert({
        "titulo": titulo,
        "valor": valor,
        "vencimento": vencimento,
        "discord_channel_id": discord_channel_id,
        "discord_user_id": discord_user_id,
    }).execute()


def contas_a_vencer(dias: int) -> list[dict]:
    hoje = datetime.now(timezone.utc).date()
    limite = (hoje + timedelta(days=dias)).isoformat()
    resposta = (
        _client.table("contas")
        .select("*")
        .eq("pago", False)
        .eq("lembrado", False)
        .lte("vencimento", limite)
        .execute()
    )
    return resposta.data


def marcar_conta_lembrada(conta_id: str):
    _client.table("contas").update({"lembrado": True}).eq("id", conta_id).execute()


def criar_gasto(descricao: str, valor: float, categoria: str | None):
    _client.table("gastos").insert({
        "descricao": descricao,
        "valor": valor,
        "categoria": categoria,
    }).execute()


def total_gastos_mes(ano: int, mes: int) -> float:
    inicio = f"{ano:04d}-{mes:02d}-01"
    if mes == 12:
        prox = f"{ano + 1:04d}-01-01"
    else:
        prox = f"{ano:04d}-{mes + 1:02d}-01"
    resposta = _client.table("gastos").select("valor").gte("data", inicio).lt("data", prox).execute()
    return sum(float(r["valor"]) for r in resposta.data)


def definir_limite_financeiro(valor: float, discord_channel_id: str, discord_user_id: str):
    _client.table("config_financeiro").upsert({
        "id": True,
        "limite_mensal": valor,
        "discord_channel_id": discord_channel_id,
        "discord_user_id": discord_user_id,
    }).execute()


def obter_config_financeiro() -> dict | None:
    resposta = _client.table("config_financeiro").select("*").eq("id", True).execute()
    return resposta.data[0] if resposta.data else None


def marcar_avisado_hoje(data_str: str):
    _client.table("config_financeiro").update({"avisado_em": data_str}).eq("id", True).execute()
