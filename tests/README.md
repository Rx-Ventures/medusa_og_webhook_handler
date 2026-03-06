# API unit tests

Run: `pytest tests/ -v`

## Why unit tests help even when we mock (and “already know the outcome”)

We mock the **service layer** (Solidgate, NetValve) so tests don’t call real APIs. The “outcome” we control is only the **return value of the mock**. What the tests actually verify is everything **between** the HTTP request and that mock:

1. **Routing and status codes**  
   The right endpoint is hit and returns the expected HTTP status (200, 400, 422, 500). If you change a URL or a status code in the route, tests fail.

2. **Request validation (Pydantic)**  
   Invalid or incomplete payloads are rejected with 422. If you add/remove required fields or change validation rules, the parametrized validation tests catch it.

3. **Response shape**  
   We assert that success responses have `success`, `message`, `data`, and that nested keys exist. If you rename a field or change the response model, tests fail and the API contract stays documented.

4. **Error handling**  
   We assert that service failures become 400, and unexpected exceptions become 500 with a safe message. If someone removes a try/except or changes error handling, tests fail.

5. **Regression safety**  
   When you refactor (e.g. rename a route, change a schema, or reorder logic), the same tests confirm that behaviour seen by the client (status + response shape) is unchanged. You don’t have to “already know” the outcome after refactors—the tests tell you.

So: we **choose** the mock’s return value, but we **verify** that the app’s HTTP layer, validation, and error handling behave correctly for many payloads and error paths. That’s what makes these tests useful beyond “we know what we mocked.”
