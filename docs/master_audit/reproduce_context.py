"""Read-only source probe; run with PYTHONPATH=<reviewed checkout>/src. Synthetic inputs only."""
import json
from dataclasses import fields
from xblago.core.llm import ChatContext
from xblago.core.memory import SemanticMemoryRecord, relevant_memory
from xblago.core.memory_policy import may_extract
from xblago.core.conversation import model_history
from xblago.core.messages import ConversationTurn
history=tuple(ConversationTurn(role='user' if i%2==0 else 'assistant',text=f'synthetic turn {i}') for i in range(30))
checks={
 'context_fields':[f.name for f in fields(ChatContext)],
 'semantic_record_fields':[f.name for f in fields(SemanticMemoryRecord)],
 'history_input_turns':len(history), 'history_visible_turns':len(model_history(history)),
 'history_first_visible':model_history(history)[0].text,
 'name_for_generic_followup':relevant_memory({'name':'Тестовый участник'},'Продолжим'),
 'direct_self_fact_extractable':may_extract('Я живу в Казани'),
 'compound_fact_question_extractable':may_extract('Я живу в Казани, что здесь посмотреть?'),
 'third_party_extractable':may_extract('Мой друг живёт в Казани'),
 'source_turn_id_available': 'source_turn_id' in {f.name for f in fields(SemanticMemoryRecord)},
}
assert checks['history_visible_turns']==24
assert checks['name_for_generic_followup']=={}
assert checks['direct_self_fact_extractable'] is True
assert checks['compound_fact_question_extractable'] is False
assert checks['third_party_extractable'] is False
assert not checks['source_turn_id_available']
print(json.dumps(checks,ensure_ascii=False,indent=2))
