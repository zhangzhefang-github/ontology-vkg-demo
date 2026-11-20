"""Transport smoke only; business acceptance stays at decision operations."""
import asyncio
import json
import os
import sys

from mcp import Client, StdioServerParameters


async def main():
    transport = StdioServerParameters(
        command=sys.executable,
        args=['-m', 'decision.mcp_server'],
        env=dict(os.environ),
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        if sorted(names) != ['query_supply_review', 'submit_supply_review']:
            raise RuntimeError(f'Unexpected Agent tools: {names}')
        result = await client.call_tool('query_supply_review', {})
        if result.is_error or result.structured_content is None:
            raise RuntimeError('MCP query failed or omitted structured content')
        proposal = await client.call_tool('submit_supply_review', {
            'order': '101', 'event': 'E01', 'agent_note': '传输检查，说明不是证据',
            'action': 'cancel_order', 'assigned_role': '采购主管',
            'description': '无法交付', 'evidence': {'fake': True},
        })
        if proposal.is_error or proposal.structured_content is None:
            raise RuntimeError('MCP proposal failed or omitted structured content')
        decision = proposal.structured_content
        if decision['action'] != 'create_supply_risk_review_task' or decision['assigned_role'] != '采购员':
            raise RuntimeError('Agent parameters changed the system action')
        if decision['description'] != '订单 101 因事件 E01 需要供应风险复核。':
            raise RuntimeError('Agent changed the system description')
        current = await client.call_tool('query_supply_review', {})
        attached = current.structured_content['rule_applications'][0]['decision']
        if attached != decision:
            raise RuntimeError('MCP query omitted or changed the occupied decision')
        print(json.dumps({'tools': names, 'decision_id': decision['id'],
                          'approval_status': decision['approval_status']}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
