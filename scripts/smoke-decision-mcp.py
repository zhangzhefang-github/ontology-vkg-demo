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
        if names != ['query_supply_review']:
            raise RuntimeError(f'Unexpected Agent tools: {names}')
        result = await client.call_tool('query_supply_review', {})
        if result.is_error or result.structured_content is None:
            raise RuntimeError('MCP query failed or omitted structured content')
        print(json.dumps({'tools': names, 'query': result.structured_content}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
