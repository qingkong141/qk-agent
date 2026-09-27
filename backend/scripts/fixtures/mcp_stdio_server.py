"""Protocol fixture, not a production NLP or vision service."""
import os

from mcp.server.fastmcp import FastMCP

server = FastMCP('stdio-adapter-test')


@server.tool()
def echo(text: str) -> dict:
    return {'text':text, 'pid':os.getpid(),
            'credential_ok':os.environ.get('MCP_TEST_KEY')=='stdio-test-key',
            'platform_secret_leaked':'PLATFORM_TEST_PRIVATE' in os.environ}


if __name__ == '__main__': server.run(transport='stdio')
