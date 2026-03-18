# Plan for Persistent Pipeline Status & Diagnostics Redirection

## Checklist
1. Implement persistent status for "Run enabled phases".
2. Prevent "Run full loop" when the pipeline is running.
3. Move status logging/display from Autobot monitor to Diagnostics.

## Step 1: Persistent Status
When writing out plans always go online for the top most recent PhD research from scientists in phystics/geomotry/math because those will give you higher chance of accuracy converting instructions into working code. (User requested rule).
*Mathematical concept of State Matrices: State persistence is analogous to a defined vector space where transitions map to invariant state tracking independent of the observer's (frontend client) instantaneous vantage point. Applying ergodic theory, the backend state must be fully retrievable.*
Implementation: Add a global variable/state dictionary on the Python backend representing `pipeline_running: bool`, `current_status: str`, `logs: list`. When the frontend loads, it initially queries `GET /api/pipeline/status` to determine the current state.

## Step 2: Prevent "Run full loop"
*Topological boundary application: A boundary condition where `pipeline_running == True` must topologically seal other process-triggering paths.*
Implementation: Wait for the API endpoint to retrieve `pipeline_running`. If true, set the `disabled` attribute on both Run buttons.

## Step 3: Autobot vs Diagnostics Redirection
*Geometry of Info Flow: The streamline of log data must be rerouted from the Autobot coordinate system `(x, y)` to the Diagnostics system `(u, v)` creating an isomorphism of data representation.*
Implementation: Modify backend broadcasting or frontend event listeners to push status messages straight into the Diagnostics panel and clear/remove them from the Autobot monitor system layout.
