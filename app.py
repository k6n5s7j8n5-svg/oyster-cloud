from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/")
async def root():
    return {"status": "running"}


@app.post("/callback")
async def callback(request: Request):
    body = await request.body()
    print(body)
    return "OK"
