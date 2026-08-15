import pandas as pd
import asyncio
import os
from langchain_core.messages import HumanMessage, SystemMessage
from graph import graph_builder  # Import your actual brain
from langchain_deepseek import ChatDeepSeek
from dotenv import load_dotenv

load_dotenv()

# Setup the Judge Model (Can be the same as your Agent)
judge_llm = ChatDeepSeek(
    model="deepseek-chat", 
    temperature=0, 
    api_key=os.getenv("HUGIN")
)

async def run_janus(question: str):
    """Runs your actual RAG agent locally (no API server needed)."""
    # Compile the graph without memory for a fresh run every time
    graph = graph_builder.compile()
    
    inputs = {"messages": [HumanMessage(content=question)]}
    final_output = ""
    
    # We just want the final answer, not the intermediate steps
    async for chunk in graph.astream(inputs, stream_mode="values"):
        last_msg = chunk["messages"][-1]
        if last_msg.type == "ai" and not last_msg.tool_calls:
            final_output = last_msg.content
            
    return final_output

async def grade_answer(question, janus_answer, ground_truth):
    """The Judge LLM compares the two answers."""
    
    prompt = f"""
    You are a strict technical judge for Formula 1 regulations.
    
    QUESTION: {question}
    GROUND TRUTH: {ground_truth}
    CANDIDATE ANSWER: {janus_answer}
    
    Evaluation Criteria:
    1. Does the Candidate Answer contain the core facts from the Ground Truth?
    2. Are the specific numbers (e.g., 350 kW, 3000 MJ/h) correct?
    3. Ignore extra conversational filler.
    
    Respond ONLY with "PASS" or "FAIL".
    """
    
    response = await judge_llm.ainvoke([HumanMessage(content=prompt)])
    return response.content.strip()

async def main():
    print("🏎️  STARTING JANUS 2.0 EVALUATION RUN...")
    
    # 1. Load Data
    df = pd.read_csv("test_data/f1_golden.csv")
    results = []

    # 2. Iterate through questions
    for index, row in df.iterrows():
        q = row['question']
        truth = row['ground_truth']
        
        print(f"\n[Test {index+1}/{len(df)}] Querying: {q}...")
        
        # A. Run RAG
        try:
            generated_answer = await run_janus(q)
        except Exception as e:
            generated_answer = f"ERROR: {str(e)}"

        # B. Run Judge
        grade = await grade_answer(q, generated_answer, truth)
        
        print(f"   👉 Grade: {grade}")
        
        results.append({
            "question": q,
            "generated_answer": generated_answer,
            "ground_truth": truth,
            "grade": grade
        })

    # 3. Calculate Scores
    results_df = pd.DataFrame(results)
    pass_rate = (results_df["grade"] == "PASS").mean() * 100
    
    print("\n" + "="*40)
    print(f"🏁 FINAL SCORE: {pass_rate:.1f}% PASS RATE")
    print("="*40)
    
    # 4. Save Report
    results_df.to_csv("test_data/eval_report.csv", index=False)
    print("📄 Report saved to test_data/eval_report.csv")

if __name__ == "__main__":
    asyncio.run(main())