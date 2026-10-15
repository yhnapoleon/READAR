"""Helpers that build Coze Studio workflow canvas JSON.

Schema learned from backend/domain/workflow/internal/canvas/examples/*.json:
  {nodes:[{id,type,meta:{position},data:{nodeMeta,inputs,outputs}}], edges:[{sourceNodeID,targetNodeID,sourcePortID}], versions}
Node type numbers: 1 Entry, 2 Exit, 3 LLM, 5 Code, 8 Selector, 9 Selector(cond)?, 12 Database, 15 Text, 18 QA, ...
Only the node kinds we need are modelled here; add more as the radar grows.
"""
from __future__ import annotations

import itertools
import json

ENTRY_ID = "100001"
EXIT_ID = "900001"
_ids = itertools.count(200001)

# variable type codes used in rawMeta.type
VT = {"string": 1, "integer": 2, "boolean": 3, "float": 4, "object": 6, "list<string>": 99, "list<object>": 103}

# From .env: MODEL_ID_0 / displayed by /api/bot/get_type_list
MODEL_TYPE = 100001
MODEL_NAME = "gpt-5-2025-08-07"


def new_id() -> str:
    return str(next(_ids))


def lit(value, vtype: str = "string") -> dict:
    return {"type": vtype, "value": {"content": value, "rawMeta": {"type": VT[vtype]}, "type": "literal"}}


def ref(block_id: str, name: str, vtype: str = "string", schema: dict | None = None) -> dict:
    d = {"type": vtype.split("<")[0] if vtype.startswith("list") else vtype,
         "value": {"content": {"blockID": block_id, "name": name, "source": "block-output"},
                   "rawMeta": {"type": VT[vtype]}, "type": "ref"}}
    if vtype.startswith("list"):
        d["type"] = "list"
        d["schema"] = schema or {"type": "string"}
    return d


def param(name: str, inp: dict) -> dict:
    return {"name": name, "input": inp}


def out(name: str, vtype: str = "string", required: bool = False, schema: dict | None = None) -> dict:
    d = {"name": name, "type": vtype, "required": required}
    if schema:
        d["schema"] = schema
    return d


def entry(outputs: list[dict], pos=(0, 0)) -> dict:
    return {"id": ENTRY_ID, "type": "1", "meta": {"position": {"x": pos[0], "y": pos[1]}},
            "data": {"nodeMeta": {"title": "开始"}, "outputs": outputs, "trigger_parameters": []}}


def exit_node(params: list[dict], pos=(1200, 0)) -> dict:
    return {"id": EXIT_ID, "type": "2", "meta": {"position": {"x": pos[0], "y": pos[1]}},
            "data": {"nodeMeta": {"title": "结束"},
                     "inputs": {"inputParameters": params, "terminatePlan": "returnVariables"}}}


def llm(node_id: str, title: str, prompt: str, system_prompt: str, params: list[dict],
        outputs: list[dict], json_mode: bool = True, max_tokens: int = 4096, pos=(600, 0)) -> dict:
    llm_param = [
        param("modelType", lit(str(MODEL_TYPE), "integer")),
        param("modleName", lit(MODEL_NAME)),
        param("generationDiversity", lit("balance")),
        param("temperature", lit("1", "float")),
        param("maxTokens", lit(str(max_tokens), "integer")),
        param("responseFormat", lit("2" if json_mode else "0", "integer")),
        param("prompt", lit(prompt)),
        param("enableChatHistory", lit(False, "boolean")),
        param("chatHistoryRound", lit("3", "integer")),
        param("systemPrompt", lit(system_prompt)),
    ]
    return {"id": node_id, "type": "3", "meta": {"position": {"x": pos[0], "y": pos[1]}},
            "data": {"nodeMeta": {"title": title, "subTitle": "大模型", "mainColor": "#5C62FF"},
                     "inputs": {"inputParameters": params, "llmParam": llm_param,
                                "settingOnError": {"processType": 1, "retryTimes": 0, "timeoutMs": 180000}},
                     "outputs": outputs}}


def code(node_id: str, title: str, params: list[dict], outputs: list[dict], source: str, pos=(600, 0)) -> dict:
    """Python code node. `source` must define `async def main(args: Args) -> Output`."""
    return {"id": node_id, "type": "5", "meta": {"position": {"x": pos[0], "y": pos[1]}},
            "data": {"nodeMeta": {"title": title, "subTitle": "代码", "mainColor": "#00B2B2"},
                     "inputs": {"inputParameters": params, "code": source, "language": 3,
                                "settingOnError": {"processType": 1, "retryTimes": 0, "timeoutMs": 60000}},
                     "outputs": outputs}}


def http_get(node_id: str, title: str, url_template: str, timeout_s: int = 60, retries: int = 1, pos=(600, 0),
             default_on_error: dict | None = None) -> dict:
    """GET request. url_template may contain {{block_output_<id>.<name>}} placeholders.
    default_on_error: if given, the node returns this output instead of failing (processType 2)."""
    on_err = {"processType": 1, "retryTimes": 0, "timeoutMs": timeout_s * 1000}
    if default_on_error is not None:
        on_err = {"switch": True, "processType": 2, "retryTimes": 0, "timeoutMs": timeout_s * 1000,
                  "dataOnErr": json.dumps(default_on_error, ensure_ascii=False)}
    node = {"id": node_id, "type": "45", "meta": {"position": {"x": pos[0], "y": pos[1]}},
            "data": {"nodeMeta": {"title": title, "subTitle": "HTTP 请求", "mainColor": "#3071F2"},
                     "inputParameters": [],
                     "inputs": {"apiInfo": {"method": "GET", "url": url_template},
                                "auth": {"authOpen": False, "authType": "CUSTOM_AUTH",
                                         "authData": {"bearerTokenData": [], "customData": {"addTo": "query", "data": []}}},
                                "body": {"bodyType": "EMPTY", "bodyData": {}},
                                "headers": [{"name": "User-Agent", "input": lit("paper-radar/0.1 (local coze studio)")}],
                                "params": [],
                                "setting": {"retryTimes": retries, "timeout": timeout_s},
                                "settingOnError": on_err},
                     "outputs": [out("body", "string"), out("statusCode", "integer"), out("headers", "string")]}}
    node["data"]["settingOnError"] = on_err
    return node


def batch(node_id: str, title: str, items_ref: dict, inner_nodes: list[dict], inner_edges: list[dict],
          outputs: list[dict], batch_size: int = 50, concurrent: int = 5, pos=(600, 0)) -> dict:
    """Batch over a list. Inner nodes read the current item via ref(node_id, 'items').
    `outputs` entries are {"name":..., "input": ref(<inner node>, <name>, list<...>)}."""
    return {"id": node_id, "type": "28", "meta": {"position": {"x": pos[0], "y": pos[1]}},
            "data": {"nodeMeta": {"title": title, "subTitle": "批处理", "mainColor": "#00B2B2"},
                     "inputs": {"batchSize": {"type": "integer", "value": {"content": str(batch_size), "type": "literal"}},
                                "concurrentSize": {"type": "integer", "value": {"content": str(concurrent), "type": "literal"}},
                                "inputParameters": [param("items", items_ref)]},
                     "outputs": outputs},
            "blocks": inner_nodes, "edges": inner_edges}


def batch_edges(batch_id: str, chain: list[str]) -> list[dict]:
    """Inner edges for a linear chain of inner node ids inside a batch."""
    e = [{"sourceNodeID": batch_id, "targetNodeID": chain[0], "sourcePortID": "batch-function-inline-output"}]
    for a, b in zip(chain, chain[1:]):
        e.append({"sourceNodeID": a, "targetNodeID": b, "sourcePortID": ""})
    e.append({"sourceNodeID": chain[-1], "targetNodeID": batch_id, "sourcePortID": "", "targetPortID": "batch-function-inline-input"})
    return e


OP = {"==": 1, "!=": 2, "len>": 3, "len>=": 4, "len<": 5, "len<=": 6, "contains": 7, "!contains": 8,
      "empty": 9, "!empty": 10, "true": 11, "false": 12, ">": 13, ">=": 14, "<": 15, "<=": 16}


def selector(node_id: str, title: str, branches: list[list[tuple]], pos=(600, 0)) -> dict:
    """branches: list of branches; each branch is a list of (left_input, op, right_input) ANDed together.
    Output ports: 'true', 'true_1', ... per branch, then 'false'."""
    bs = []
    for conds in branches:
        bs.append({"condition": {"logic": 2, "conditions": [
            {"left": {"input": l}, "operator": OP[o], "right": {"input": r}} for l, o, r in conds]}})
    return {"id": node_id, "type": "8", "meta": {"position": {"x": pos[0], "y": pos[1]}},
            "data": {"nodeMeta": {"title": title, "subTitle": "选择器", "mainColor": "#00B2B2"},
                     "inputs": {"branches": bs}}}


def custom_sql(node_id: str, title: str, database_id: str, sql: str, params: list[dict], row_schema: list[dict], pos=(600, 0)) -> dict:
    """DatabaseCustomSQL. Use '{{param}}' (quoted) placeholders in sql."""
    return {"id": node_id, "type": "12", "meta": {"position": {"x": pos[0], "y": pos[1]}},
            "data": {"nodeMeta": {"title": title, "subTitle": "SQL 自定义", "mainColor": "#FF811A"},
                     "inputs": {"databaseInfoList": [{"databaseInfoID": database_id}], "inputParameters": params, "sql": sql},
                     "outputs": [{"type": "list", "name": "outputList", "schema": {"type": "object", "schema": row_schema}},
                                 out("rowNum", "integer")]}}


def knowledge_retriever(node_id: str, title: str, dataset_ids: list[str], query_input: dict, top_k: int = 5,
                        min_score: float = 0.3, pos=(600, 0)) -> dict:
    return {"id": node_id, "type": "6", "meta": {"position": {"x": pos[0], "y": pos[1]}},
            "data": {"nodeMeta": {"title": title, "subTitle": "知识库检索", "mainColor": "#FF811A"},
                     "inputs": {"datasetParam": [
                         {"name": "datasetList", "input": {"type": "list", "schema": {"type": "string"}, "value": {"type": "literal", "content": dataset_ids}}},
                         {"name": "topK", "input": {"type": "integer", "value": {"type": "literal", "content": top_k}}},
                         {"name": "useRerank", "input": {"type": "boolean", "value": {"type": "literal", "content": False, "rawMeta": {"type": 3}}}},
                         {"name": "useRewrite", "input": {"type": "boolean", "value": {"type": "literal", "content": False, "rawMeta": {"type": 3}}}},
                         {"name": "isPersonalOnly", "input": {"type": "boolean", "value": {"type": "literal", "content": False, "rawMeta": {"type": 3}}}},
                         {"name": "minScore", "input": {"type": "float", "value": {"type": "literal", "content": min_score}}},
                         {"name": "strategy", "input": {"type": "integer", "value": {"type": "literal", "content": 1}}}],
                         "inputParameters": [param("Query", query_input)]},
                     "outputs": [{"type": "list", "name": "outputList", "schema": {"type": "object", "schema": [out("output", "string")]}}]}}


def edge(src: str, dst: str, port: str = "") -> dict:
    return {"sourceNodeID": src, "targetNodeID": dst, "sourcePortID": port}


def canvas(nodes: list[dict], edges: list[dict]) -> str:
    return json.dumps({"nodes": nodes, "edges": edges, "versions": {"loop": "v2"}}, ensure_ascii=False)
