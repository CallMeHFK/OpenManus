import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Optional

from colorama import Fore, init
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

# Add current directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, current_dir)

# Add root directory to Python path
root_dir = os.path.dirname(parent_dir)
sys.path.insert(0, root_dir)
from app.config import config

# Initialize colorama
def init_colorama():
    init(autoreset=True)

class OpenManusClient:
    def __init__(self):
        # Load configuration
        # self.config = load_config()

        # Initialize session and client objects
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()

        # Initialize AsyncOpenAI client with config
        self.llm_config = config.llm["default"]
        api_key = self.llm_config.api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenAI API key not found in config.toml or environment variables"
            )

        self.openai_client = AsyncOpenAI(
            api_key=api_key, base_url=self.llm_config.base_url
        )

    async def connect_to_server(self, server_script_path: str = None):
        """Connect to the openmanus MCP server"""
        # Use provided path or default from config
        script_path = server_script_path

        server_params = StdioServerParameters(
            command="python", args=[script_path], env=None
        )

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )

        await self.session.initialize()

        # List available tools
        response = await self.session.list_tools()
        tools = response.tools
        print("\nConnected to server with tools:", [tool.name for tool in tools])

    async def chat_loop(self):
        """Run an interactive chat loop for testing tools"""
        print(Fore.CYAN + "\n🚀 OpenManus MCP Client Started!")
        print(Fore.GREEN + "Type your queries or 'quit' to exit.")
        print(
            Fore.YELLOW
            + "Example query: 'What is the recent news about the stock market?'\n"
        )

        while True:
            try:
                query = input(Fore.BLUE + "🔍 Query: ").strip()

                if query.lower() == "quit":
                    print(Fore.RED + "👋 Exiting... Goodbye!")
                    break

                response = await self.process_query(query)
                print(Fore.MAGENTA + "\n💬 Response: " + response)

            except Exception as e:
                print(Fore.RED + f"\n❌ Error: {str(e)}")

    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()
        await self.openai_client.close()  # Close the OpenAI client

    async def process_query(self, query: str) -> str:
        """Process a query using LLM and available tools"""
        # Add a system message to set the context for the model
        messages = [
            {
                "role": "system",
                "content": "You are a general-purpose AI assistant called OpenManus. You can help users complete a wide range of tasks, providing detailed information and assistance as needed. Please include emojis in your responses to make them more engaging.",
            },
            {"role": "user", "content": query},
        ]

        response = await self.session.list_tools()
        available_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                },
            }
            for tool in response.tools
        ]
        # Initial LLM API call
        response = await self.openai_client.chat.completions.create(
            model=self.llm_config.model,
            messages=messages,
            tools=available_tools,
            tool_choice="auto",
        )

        # Process response and handle tool calls
        final_text = []

        while True:
            message = response.choices[0].message

            # Add assistant's message to conversation
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content if message.content else None,
                    "tool_calls": message.tool_calls
                    if hasattr(message, "tool_calls")
                    else None,
                }
            )

            # If no tool calls, we're done
            if not hasattr(message, "tool_calls") or not message.tool_calls:
                if message.content:
                    final_text.append(message.content)
                break

            # Handle tool calls
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = tool_call.function.arguments

                # Convert tool_args from string to dictionary if necessary
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except (ValueError, SyntaxError) as e:
                        print(f"Error converting tool_args to dict: {e}")
                        tool_args = {}

                # Ensure tool_args is a dictionary
                if not isinstance(tool_args, dict):
                    tool_args = {}

                # Execute tool call
                print(f"Calling tool {tool_name} with args: {tool_args}")
                result = await self.session.call_tool(tool_name, tool_args)
                final_text.append(f"[Calling tool {tool_name}]")
                # final_text.append(f"Result: {result.content}")

                # Add tool result to messages
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(result.content),
                    }
                )

            # Get next response from LLM
            response = await self.openai_client.chat.completions.create(
                model=self.llm_config.model,
                messages=messages,
                tools=available_tools,
                tool_choice="auto",
            )

        return "\n".join(final_text)


async def main():
    if len(sys.argv) > 1:
        server_script = sys.argv[1]
    else:
        server_script = "mcp/server/server.py"

    client = OpenManusClient()
    try:
        await client.connect_to_server(server_script)
        await client.chat_loop()
    finally:
        await client.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
