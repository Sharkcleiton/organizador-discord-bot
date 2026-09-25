import os
import json
from datetime import datetime

from google import genai
from google.genai import types

_cliente = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

MODELO = "gemini-3.5-flash-lite"

INSTRUCOES = """Você é o assistente organizador pessoal do usuário, dentro de um canal do Discord.
Sua função é ler a mensagem dele e decidir o que fazer, respondendo SEMPRE em JSON puro, sem markdown, no formato:

{"acoes": [ {"tipo": "tarefa"|"lembrete"|"meta"|"pergunta"|"resposta", ...} ]}

Tipos de ação:
- "tarefa": {"tipo":"tarefa","titulo":str,"descricao":str ou null,"prazo":"YYYY-MM-DD" ou null}
- "lembrete": {"tipo":"lembrete","titulo":str,"disparar_em":"YYYY-MM-DDTHH:MM:SS"}
- "meta": {"tipo":"meta","titulo":str,"descricao":str ou null,"tipo_meta":"semanal"|"mensal"|"anual","prazo":"YYYY-MM-DD" ou null}
- "pergunta": quando faltar informação para decidir (ex: não sabe se vira tarefa ou lembrete, ou falta prazo/horário) —
  {"tipo":"pergunta","pergunta":str,"contexto":{...guarde aqui o que você já entendeu da mensagem, para completar quando o usuário responder...}}
- "resposta": só uma resposta de texto simples, sem criar nada — {"tipo":"resposta","texto":str}

Regras:
- Uma mensagem pode gerar VÁRIAS ações (ex: duas tarefas na mesma frase).
- Sempre que o usuário der um prazo relativo (ex: "daqui 3h", "amanhã de manhã"), calcule a data/hora absoluta usando a data/hora atual informada abaixo.
- Se existir um "contexto pendente" (uma pergunta sua anterior que o usuário está respondendo agora), use-o para montar a ação final — não pergunte de novo a mesma coisa.
- Nunca invente prazo ou horário que o usuário não deu — se não der pra saber, pergunte.
- Responda só o JSON, nada de texto fora dele.
"""


async def processar_mensagem(texto: str, pendente: dict | None = None) -> dict:
    agora = datetime.now().isoformat()
    contexto = (
        f"\nContexto pendente da pergunta anterior: {json.dumps(pendente, ensure_ascii=False)}"
        if pendente
        else ""
    )
    prompt = f"{INSTRUCOES}\n\nData/hora atual: {agora}{contexto}\n\nMensagem do usuário: {texto}"

    resposta = await _cliente.aio.models.generate_content(
        model=MODELO,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    try:
        return json.loads(resposta.text)
    except (json.JSONDecodeError, AttributeError):
        return {"acoes": [{"tipo": "resposta", "texto": "Desculpa, não entendi direito — pode reformular?"}]}
