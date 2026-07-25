# Workflows

## Overview

Workflows are YAML-defined multi-step pipelines that chain LLM calls, tool executions, and shell commands with conditions, loops, and parallelism.

## Usage

```
> /workflow path/to/workflow.yaml
```

## Workflow Structure

```yaml
name: my_workflow
description: What this workflow does
variables:
  api_url: "https://api.example.com"
  retry_count: 3

steps:
  - name: step1
    type: llm
    prompt: "Analyze this: {{input}}"
    output: analysis

  - name: step2
    type: shell
    command: "curl {{api_url}}/data"
    output: api_data
    condition: "analysis"

  - name: step3
    type: parallel
    steps:
      - name: task_a
        type: shell
        command: "process_a {{api_data}}"
      - name: task_b
        type: shell
        command: "process_b {{api_data}}"
```

## Step Types

### LLM Step

Sends a prompt to an AI model:

```yaml
- name: summarize
  type: llm
  prompt: "Summarize: {{content}}"
  output: summary
```

### Shell Step

Executes a shell command:

```yaml
- name: list_files
  type: shell
  command: "ls -la"
  output: file_list
```

### Tool Step

Invokes a registered tool:

```yaml
- name: read_config
  type: tool
  tool_name: filesystem.read
  arguments:
    path: "./config.json"
  output: config_data
```

### Parallel Step

Runs sub-steps concurrently:

```yaml
- name: parallel_tasks
  type: parallel
  steps:
    - name: task1
      type: shell
      command: "echo task1"
    - name: task2
      type: shell
      command: "echo task2"
```

### Loop Step

Iterates over a collection:

```yaml
- name: process_files
  type: loop
  items: "{{file_list}}"
  item_var: file
  steps:
    - name: process
      type: shell
      command: "wc -l {{file}}"
```

## Variables and Templates

Variables use `{{name}}` or `${name}` syntax. Step outputs are captured via `output:` and available in later steps:

```yaml
variables:
  greeting: hello

steps:
  - name: step1
    type: shell
    command: "echo {{greeting}}"
    output: result

  - name: step2
    type: llm
    prompt: "The previous step said: {{result}}"
```

## Conditions

Steps can have conditions that determine whether they run:

```yaml
- name: deploy
  type: shell
  command: "deploy.sh"
  condition: "tests_passed"
```

Condition operators:

- **Variable truthy** — `condition: "var_name"` (runs if var is truthy and exists)
- **Equality** — `condition: "a == b"` (compares variable values)
- **Inequality** — `condition: "a != b"`
- **Containment** — `condition: "x in items"` (checks if value of x is in items)

## Execution Events

During workflow execution, events are emitted:

| Event | Data |
|-------|------|
| `start` | Step count, workflow name |
| `step_start` | Step name, type |
| `step_done` | Status, output preview, elapsed time |
| `done` | Completed/failed/skipped counts, total time |
| `error` | Error message |
