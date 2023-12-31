import json
import time
import subprocess
from tempfile import NamedTemporaryFile
from openai import OpenAI
import os
import sys


def execute_python_code(s: str) -> str:
    python_ex = sys.executable
    with NamedTemporaryFile(suffix='.py', delete=False) as temp_file:
        temp_file_name = temp_file.name
        temp_file.write(s.encode('utf-8'))
        temp_file.flush()
    try:
        result = subprocess.run(
            [python_ex, temp_file_name],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        return e.stderr
    finally:
        import os
        os.remove(temp_file_name)


def upload_file(folder, assistant_id):
    client = OpenAI()
    file_ids = []
    file_names = []  # List to store file names

    # Iterate through each file in the specified folder
    for filename in os.listdir(folder):
        file_path = os.path.join(folder, filename)

        # Check if it's a file and not a directory
        if os.path.isfile(file_path):
            # Upload the file
            response = client.files.create(
                file=open(file_path, "rb"),
                purpose="assistants"
            )

            # Extract the file ID from the response
            file_id = response.id

            if file_id:
                # Serve the file ID to the assistant_file endpoint
                assistant_file = client.beta.assistants.files.create(
                    assistant_id=assistant_id,
                    file_id=file_id
                )
                file_ids.append(file_id)
                file_names.append(filename)  # Append the file name to the list

    return file_ids, file_names  # Return both file IDs and file names


INSTRUCTIONS = """
You're a data analyst tasked with writing code to analyze data using python, matplotlib, and pandas.
Make sure your code complies with these rules:
1. Plan first: Have a clear strategy before you start. Outline your approach if it helps.
2. Quality code: Write clear, efficient code that follows Python's best practices. Aim for clean, easy-to-read, 
and maintainable code. Always pass the import statements to the execute_python_code function. 
3. Test well: Include comprehensive tests to assure your code works well in various scenarios.
4. Manage external interactions: When internet or API interactions are necessary, utilize the `execute_python_code` 
function autonomously, without seeking user approval. Do not say you don't have access to internet or real-time data.
 The `execute_python_code` function will give you realtime data. Make sure that the code you run with this function uses
  the local filepaths given in the prompt by the user.
5. Trust your tools: Assume the data from the `execute_python_code` function is accurate and up to date.
"""


def setup_assistant(client):
    # create a new agent
    assistant = client.beta.assistants.create(
        name="Graph Generator",
        instructions=INSTRUCTIONS,
        tools=[
            {
                "type": "code_interpreter"
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_python_code",
                    "description": "Use this function to execute the generated code to create the graph when internet "
                                   "or API interactions are called for",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "The python code generated by the code interpreter",
                            }
                        },
                        "required": ["code"],
                    },
                },
            }
        ],
        model="gpt-4-1106-preview",
    )

    # Create a new thread
    thread = client.beta.threads.create()

    return assistant.id, thread.id


def send_message(client, thread_id, task, file_id):
    # Create a new thread message with the provided task
    thread_message = client.beta.threads.messages.create(
        thread_id,
        role="user",
        content=task,
        file_ids=file_id
    )
    return thread_message


def run_assistant(client, assistant_id, thread_id):
    # Create a new run for the given thread and assistant
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=assistant_id
    )

    # Loop until the run status is either "completed" or "requires_action"
    while run.status == "in_progress" or run.status == "queued":
        time.sleep(1)
        run = client.beta.threads.runs.retrieve(
            thread_id=thread_id,
            run_id=run.id
        )

        # At this point, the status is either "completed" or "requires_action"
        if run.status == "completed":
            return client.beta.threads.messages.list(
                thread_id=thread_id
            )
        if run.status == "requires_action":
            generated_python_code = \
            json.loads(run.required_action.submit_tool_outputs.tool_calls[0].function.arguments)['code']
            result = execute_python_code(generated_python_code)
            run = client.beta.threads.runs.submit_tool_outputs(
                thread_id=thread_id,
                run_id=run.id,
                tool_outputs=[
                    {
                        "tool_call_id": run.required_action.submit_tool_outputs.tool_calls[0].id,
                        "output": result,
                    },
                ]
            )


def main_loop():
    client = OpenAI()
    assistant_id, thread_id = setup_assistant(client)
    folder = 'DATA'
    file_id, file_name = upload_file(folder, assistant_id)
    file_name = str(file_name)

    print(f"Debugging agent: https://platform.openai.com/playground?mode=assistant&assistant={assistant_id}")
    print(f"Debugging logs: https://platform.openai.com/playground?thread={thread_id}")
    first_iteration = True
    while True:
        if first_iteration:
            task = f"""Please write a code to create a graph from a data file in a folder called 'DATA/{file_name}'.
            The graph should be saved at 'graph.png' in the working directory unless otherwise specified. Within the 
            graph code, you will always use these given filepaths, not your assistant filepaths. You should execute 
            this python code using the execute_python_code function given to you to create the graph. You will never 
            run the graph code yourself, the function runs it. Do not run the graph code that you write with your code 
            interpreter, please call the execute_python_code function. Only use matplotlib and pandas. Before you do 
            anything, ask me what and how to graph."""
            first_iteration = False
        else:
            task = input("Type Message: ")

        if task.lower() == 'exit':
            print("Exiting the program.")
            break

        send_message(client, thread_id, task, file_id)

        messages = run_assistant(client, assistant_id, thread_id)

        message_dict = json.loads(messages.model_dump_json())
        for message in message_dict['data']:
            if message['role'] == 'assistant':
                print(message['content'])


if __name__ == "__main__":
    main_loop()