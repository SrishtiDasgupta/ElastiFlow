## Steep Workflow

The API specified by Steep is quite extensive, and will not be implemented in one shot. Features that are already implemented are going to be listed here.

| Workflows |          |
|-----------|----------|
|api|Currently only accept 4.7.0|
|name||
|priority||
|retries||
|vars|See variables section|
|actions| See actions section|


| Variables |          |
|-----------|----------|
|id| [x]|
|value| [x]|


| Execute actions |          |
|-----------|----------|
|id|[ ]|
|type|[x]|
|service|has to be a path|
|inputs|[x]|
|outputs|[x]|
|dependsOn|[ ]|
|retries|[ ]|
|maxInactivity|[ ]|
|maxRuntime|[ ]|
|deadline|[ ]|


| For-each actions |          |
|-----------|----------|
|id|[ ]|
|type|[x]|
|input|[x]|
|enumerator|[x]|
|output|[x]|
|dependsOn|[ ]|
|actions|[ ]|
|yieldToOutput|[ ]|
|yieldToInput|[x]|

| Include actions |          |
|-----------|----------|
|id|[ ]|
|type|[ ]|
|macro|[ ]|
|inputs|[ ]|
|outputs|[ ]|
|dependsOn|[ ]|

| Parameters |          |
|-----------|----------|
|id|[ ]|
|var|[ ]|
|value|[ ]|

| Output parameters |          |
|-----------|----------|
|id|[ ]|
|var|[ ]|
|prefix|[ ]|
|store|[ ]|

| Include output parameters ||
|-----------|----------|
|id|[ ]|
|var|[ ]|

| Retry policy defaults |          |
|-----------|----------|
|processChains|[ ]|

