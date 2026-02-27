from fastapi import Request

@app.post("/callback")
async def callback(request: Request):
    body = await request.body()
    print(body)
    return "OK"
