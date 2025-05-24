from fastapi import FastAPI, APIRouter
from pydantic import BaseModel
import httpx
from typing import List, Optional
import fitz  # PyMuPDF
from fastapi import UploadFile, File
import base64
import easyocr
from PIL import Image
import numpy as np
import io

from fastapi.middleware.cors import CORSMiddleware

reader = easyocr.Reader(['en', 'fr'])  # Add languages as needed

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (can be restricted to specific domains)
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, OPTIONS, etc.)
    allow_headers=["*"],  # Allow all headers
)
class Message(BaseModel):
    sender: str  # 'user' or 'bot'
    text: str

class PromptRequest(BaseModel):
    prompt: str
    model: str
    history: Optional[List[Message]] = []

uploaded_docs = {}

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        return {"error": "Only PDF files are supported"}

    content = await file.read()
    pdf_path = f"/tmp/{file.filename}"
    with open(pdf_path, "wb") as f:
        f.write(content)

    # Parse PDF content
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()

    uploaded_docs["latest"] = text
    return {"message": "PDF uploaded and parsed successfully"}

async def summarize_history(history: List[Message], model: str = "smollm2") -> Message:
    conversation_text = "\n".join([f"{m.sender}: {m.text}" for m in history])
    prompt = f"You are a synthesis writer expert. You must stay concise with short sentences. Summarize the following conversation in max 3 sentences:\n{conversation_text}\nSummary:"

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.post(
            "http://ollama:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": False}
        )
        summary = response.json().get("response", "").strip()
        return Message(sender="system", text=f"[Summary]: {summary}")

@app.post("/ask")
async def ask_bot(request: PromptRequest):

    doc_context = uploaded_docs.get("latest", "")

    # Determine if summarization is needed
    if len(request.history) > 5:
        summary_message = await summarize_history(request.history[:-5], model=request.model)
        trimmed_history = [summary_message] + request.history[-5:]
    else:
        trimmed_history = request.history

    # Format chat history into prompt
    history_prompt = "\n".join(f"{m.sender}: {m.text}" for m in trimmed_history)

    # Combine document context and history
    if doc_context:
        full_prompt = f"Document context:\n{doc_context[:3000]}\n\nPrevious conversation:\n{history_prompt}\nNew user input: {request.prompt}"
    else:
        full_prompt = f"Previous conversation:\n{history_prompt}\nNew user input: {request.prompt}\nAnswer simply and remember you are a small model with small capabilities"

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.post(
            "http://ollama:11434/api/generate",
            json={
                "model": request.model,
                "prompt": full_prompt,
                "stream": False  # 👈 ADD THIS!
            }
        )
        result = response.json()
        return {"response": result.get("response")}

@app.post("/reset")
async def reset_context():
    uploaded_docs["latest"] = ""
    return {"status": "reset"}

@app.post("/ocr")
async def extract_text(image: UploadFile = File(...)):
    contents = await image.read()

    encoded = base64.b64encode(contents).decode("utf-8")

    payload = {
        "model": "moondream",
        "prompt": "Describe this image",
        "images": [encoded],
        "stream": False
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.post("http://ollama:11434/api/generate", json=payload)
    result = response.json()

    #extract text with easy ocr
    image_np = np.array(Image.open(io.BytesIO(contents)).convert("RGB"))
    results = reader.readtext(image_np, detail=0)
    extracted_text = "|".join(results)
    return {"text": "📷 "+ result.get("response", "") + "\n(extracted text content is : " + extracted_text + ")"}
