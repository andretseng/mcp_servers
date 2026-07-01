"""Spin the server up over stdio as a real MCP client, exercise live tools."""
import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command="uv",
        args=["run", "--project", "/home/andre/tools/tek-scope-mcp", "tek-scope-mcp"],
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            idn = await s.call_tool("check_connection", {})
            print("IDN:", idn.content[0].text)

            m = await s.call_tool("measure", {"channel": 1, "meas_type": "FREQuency"})
            print("MEAS:", m.content[0].text)

            shot = await s.call_tool("get_screenshot", {})
            print("SHOT isError:", shot.isError)
            print("SHOT raw:", repr(shot.content)[:600])


asyncio.run(main())
