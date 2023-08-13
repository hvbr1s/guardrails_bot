import os
import json
from dotenv import main
import pinecone
import openai
from fastapi import FastAPI, Request, HTTPException, status, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, parse_obj_as
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from starlette.status import HTTP_429_TOO_MANY_REQUESTS
import httpx
from nostril import nonsense
import tiktoken
from nemoguardrails import LLMRails, RailsConfig #New

main.load_dotenv()

#########Initialize backend API keys ######

server_api_key=os.environ['BACKEND_API_KEY'] #New
API_KEY_NAME=os.environ['API_KEY_NAME'] #New
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key_header: str = Depends(api_key_header)):
    if api_key_header.split(' ')[1] != server_api_key:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    return api_key_header

class Query(BaseModel):
    user_input: str
    user_id: str

# Initialize Pinecone
openai.api_key=os.environ['OPENAI_API_KEY']
pinecone.init(api_key=os.environ['PINECONE_API_KEY'], enviroment=os.environ['PINECONE_ENVIRONMENT'])
pinecone.whoami()
index_name = 'hc'
#index_name = 'academyzd'
index = pinecone.Index(index_name)

embed_model = "text-embedding-ada-002"


# Chat system prompt
primer = """

You are Samantha, a highly intelligent and helpful virtual assistant designed to support Ledger. Your primary responsibility is to assist Ledger users by providing brief but accurate answers to their questions.

Users may ask about various Ledger products, including the Nano X (Bluetooth, large storage, has a battery), Nano S Plus (large storage, no Bluetooth, no battery), Ledger Stax, and the Ledger Live app.
The official Ledger store is located at https://shop.ledger.com/. For authorized resellers, please visit https://www.ledger.com/reseller/. Do not modify or share any other links for these purposes.

When users inquire about tokens, crypto or coins supported in Ledger Live, it is crucial to strictly recommend checking the Crypto Asset List link to verify support: https://support.ledger.com/hc/en-us/articles/10479755500573?docs=true/. Do NOT provide any other links to the list.

VERY IMPORTANT:

- Use the provided CONTEXT and CHAT HISTORY to help you answer users' questions
- When responding to a question, include a maximum of two URL links from the provided CONTEXT.
- If the question is unclear or not about Ledger products, disregard the CONTEXT and invite any Ledger-related questions using this exact response: "I'm sorry, I didn't quite understand your question. Could you please provide more details or rephrase it? Remember, I'm here to help with any Ledger-related inquiries."
- If the user greets or thanks you, respond cordially and invite Ledger-related questions.
- Always present URLs as plain text, never use markdown formatting.
- If a user requests to speak with a human agent or if you believe they should speak to a human agent, don't share any links, instead invite them to open a ticket or start a live chat.
- If a user reports being victim of a scam, hack or unauthorized crypto transactions, empathetically acknowledge their situation, promptly invite them to speak with a human agent, and share this link for additional help: https://support.ledger.com/hc/en-us/articles/7624842382621-Loss-of-funds?support=true.
- If a user needs to reset their device, they must always ensure they have their recovery phrase on hand before proceeding with the reset.
- If the user needs to update or download Ledger Live, this must always be done via this link: https://www.ledger.com/ledger-live
- If asked about Ledger Stax, inform the user it's not yet released, but pre-orderers will be notified via email when ready to ship. Share this link for more details: https://support.ledger.com/hc/en-us/articles/7914685928221-Ledger-Stax-FAQs.
- The Ledger Recover service is not available just yet. When it does launch, keep in mind that it will be entirely optional. Even if you update your device firmware, it will NOT automatically activate the Recover service. Learn more: https://support.ledger.com/hc/en-us/articles/9579368109597-Ledger-Recover-FAQs
- If you see the error "Something went wrong - Please check that your hardware wallet is set up with the recovery phrase or passphrase associated to the selected account", it's likely your Ledger's recovery phrase doesn't match the account you're trying to access.

Begin!

"""

# Function to read content from a file
def read_file_content(file_path):
    with open(file_path, "r") as f:
        return f.read()
    
yaml_content = read_file_content("/home/danledger/knowledge_bot/config/config.yml")
rag_colang_content = read_file_content("/home/danledger/knowledge_bot/config/topics.co")


# #####################################################

# Initialize Rails config
config = RailsConfig.from_content(
    colang_content=rag_colang_content,
    yaml_content=yaml_content
)
# Create Rails
rag_rails = LLMRails(config)

tokenizer = tiktoken.get_encoding('cl100k_base')
# create the length function
def tiktoken_len(text):
    tokens = tokenizer.encode(
        text,
        disallowed_special=()
    )
    return len(tokens)

def get_user_id(request: Request):
    try:
        body = parse_obj_as(Query, request.json())
        user_id = body.user_id
        return user_id
    except Exception as e:
        return get_remote_address(request)


# Define FastAPI app
app = FastAPI()
# app.mount("/static", StaticFiles(directory="static"), name="./home/dan/langchain_projects/hcBot/static/BBALP00A.TTF")
# templates = Jinja2Templates(directory="templates")

# Define limiter
limiter = Limiter(key_func=get_user_id)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

@app.exception_handler(RateLimitExceeded)
async def custom_rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Too many requests, please try again in a minute."},
    )

# Initialize user state
user_states = {}


# Define FastAPI endpoints
@app.get("/")
async def root():
    return {"welcome": "You've reached the home route!"}

@app.get("/_health")
async def health_check():
    return {"status": "OK"}

@app.get("/_index")
async def pinecone_index():
    return {"index": index_name}

@app.post('/gpt')
@limiter.limit("10/minute")
async def react_description(query: Query, request: Request, api_key: str = Depends(get_api_key)): #New
    user_id = query.user_id
    user_input = query.user_input.strip()
    if user_id not in user_states:
        user_states[user_id] = None
    last_response = user_states[user_id]
    if not user_input or nonsense(user_input):
        print('Nonsense detected!')
        return {'output': "I'm sorry, I cannot understand your question. Could you please provide more details or rephrase it? Remember, I'm here to help with any Ledger-related inquiries."}
    else:
    
        try:

            # Retrieve relevant chunks from Pinecone and build augmented query
            async def retrieve(query, contexts=None):
                res_embed = openai.Embedding.create(
                    input=[user_input],
                    engine=embed_model
                )
                xq = res_embed['data'][0]['embedding']
                res_query = index.query(xq, top_k=2, include_metadata=True)
                contexts = [(item['metadata']['text'] + "\nLearn more: " + item['metadata'].get('source', 'N/A')) for item in res_query['matches'] if item['score'] > 0.80]
                prev_response_line = f"Assistant: {last_response}\n" if last_response else ""
                augmented_query = "CONTEXT: " + "\n\n-----\n\n" + "\n\n---\n\n".join(contexts) + "\n\n-----\n\n"+ "CHAT HISTORY: \n" + prev_response_line + "User: " + user_input + "\n" + "Assistant: "
                return augmented_query
            
            augmented_query = await retrieve(user_input)
            print(augmented_query)
    
            async def rag(query, contexts=None):
                print("RAG > Called!")
                res = openai.ChatCompletion.create(
                    temperature=0.0,
                    model='gpt-4',
                    #model="gpt-3.5-turbo-0613",
                    messages=[
                        {"role": "system", "content": primer},
                        {"role": "user", "content": augmented_query}
                    ]
                )             
                reply = res['choices'][0]['message']['content']
                return reply
            
            #response = await rag(augmented_query)
            rag_rails.register_action(action=retrieve, name="retrieve")
            rag_rails.register_action(action=rag, name="rag")
            response = await rag_rails.generate_async(prompt=user_input)

            # Counting tokens
            count_response = tiktoken_len(response)
            count_query = tiktoken_len(augmented_query)
            count_sysprompt = tiktoken_len(primer)
            total_input_tokens = count_sysprompt + count_query
            print("Total input tokens: " + str(total_input_tokens))
            total_output_tokens = count_response
            print("Total output tokens: " + str(total_output_tokens))
            final_count = total_output_tokens + total_input_tokens
            print("Total tokens: " + str(final_count))
            total_price = str(((total_input_tokens / 1000) * 0.03) + ((total_output_tokens / 1000) * 0.06))
            print("Total price for GPT4 call: " + total_price + " $USD")
    
            # Save the response to the global variable
            last_response = response
    
            # Save the response to a thread
            user_states[user_id] = response
            print(user_states)
    
            print(response)
            return {'output': response}
    
        except ValueError as e:
            print(e)
            raise HTTPException(status_code=400, detail="Invalid input")
        
# Start command
# uvicorn app_test:app --reload --port 8008
