"""The external Agent can query; human operations are never MCP tools."""
from mcp.server import MCPServer

from decision.service import DecisionService, QueryResponse, DecisionResponse

server = MCPServer('procurement-supply-review')


@server.tool()
def query_supply_review() -> QueryResponse:
    """查询需要供应风险复核的订单及依据；未命中不表示安全。"""
    return DecisionService().query()


@server.tool()
def submit_supply_review(order: str, event: str, agent_note: str = '') -> DecisionResponse:
    """点名订单与事件提交建议；动作和证据由系统生成，说明不是证据。"""
    return DecisionService().submit(order, event, agent_note)


if __name__ == '__main__':
    server.run(transport='stdio')
