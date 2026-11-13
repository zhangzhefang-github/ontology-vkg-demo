"""The external Agent can query; human operations are never MCP tools."""
from mcp.server import MCPServer

from decision.service import DecisionService, QueryResponse

server = MCPServer('procurement-supply-review')


@server.tool()
def query_supply_review() -> QueryResponse:
    """查询需要供应风险复核的订单及依据；未命中不表示安全。"""
    return DecisionService().query()


if __name__ == '__main__':
    server.run(transport='stdio')
