import os
import asyncio
import pandas as pd
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision
from langchain_deepseek import ChatDeepSeek
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.messages import HumanMessage
from config import EMBEDDING_MODEL
from graph import graph_builder
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
# 1. Setup the Judge (DeepSeek)
judge_llm = ChatDeepSeek(
    model="deepseek-chat", 
    temperature=0, 
    api_key=os.getenv("HUGIN")
)

# 2. Setup the Embeddings (Must match what you used for Pinecone)
embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

async def run_janus_with_tracing(question: str):
    """
    Runs the agent and captures TWO things:
    1. The Final Answer
    2. The Retrieved Contexts (what the Tool returned)
    """
    graph = graph_builder.compile()
    inputs = {"messages": [HumanMessage(content=question)]}
    
    final_answer = ""
    retrieved_contexts = []
    
    async for chunk in graph.astream(inputs, stream_mode="values"):
        # We look at the latest message in the conversation
        messages = chunk.get("messages", [])
        if not messages:
            continue
            
        last_msg = messages[-1]
        
        # CAPTURE CONTEXT: If it's a Tool Message, save the content
        if last_msg.type == "tool":
            # The tool returns a string of citations. Ragas needs a list of strings.
            # We treat the whole tool output as one context block.
            retrieved_contexts.append(last_msg.content)
            
        # CAPTURE ANSWER: If it's an AI Message (and not a tool call request)
        if last_msg.type == "ai" and not last_msg.tool_calls:
            final_answer = last_msg.content
            
    # Fallback if no context was retrieved (e.g., pure chat)
    if not retrieved_contexts:
        retrieved_contexts = ["No context retrieved."]
        
    return final_answer, retrieved_contexts

async def main():
    print("🧪 STARTING RAGAS EVALUATION PIPELINE...")
    
    # 1. Load your Golden Data
    # Ensure you fixed the CSV based on our last chat!
    df = pd.read_csv("test_data/f1_golden.csv")
    
    questions = []
    answers = []
    contexts = []
    ground_truths = []
    
    # 2. Generate Answers & Capture Context
    for index, row in df.iterrows():
        q = row['question']
        gt = row['ground_truth']
        
        print(f"[{index+1}/{len(df)}] Processing: {q[:30]}...")
        
        ans, ctx = await run_janus_with_tracing(q)
        
        questions.append(q)
        answers.append(ans)
        contexts.append(ctx) # Ragas expects a list of strings here
        ground_truths.append(gt)

    # 3. Prepare Dataset for Ragas
    data_dict = {
        "question": questions,
        "answer": answers,
        "contexts": contexts,
        "ground_truth": ground_truths
    }
    dataset = Dataset.from_dict(data_dict)

    print("\n⚖️  JUDGE IS DELIBERATING (This may take a minute)...")
    
    # 4. Run Evaluation
    # We pass our DeepSeek model as the 'llm' so Ragas doesn't look for OpenAI
    results = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision],
        llm=judge_llm, 
        embeddings=embeddings
    )

    print("\n" + "="*40)
    print(results)
    print("="*40)
    
    # 5. Save Results
    results.to_pandas().to_csv("test_data/ragas_report.csv", index=False)
    print("📄 Detailed Ragas report saved to test_data/ragas_report.csv")

if __name__ == "__main__":
    asyncio.run(main())