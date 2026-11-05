import os
from crewai import Agent, Task, Crew, Process
from langchain_openai import ChatOpenAI

# Ensure OpenRouter key is properly recognized
api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
if api_key:
    os.environ["OPENROUTER_API_KEY"] = api_key

# Configure OpenRouter with an active model
openrouter_llm = ChatOpenAI(
    model="openai/gpt-4o-mini",
    openai_api_key=api_key,
    openai_api_base="https://openrouter.ai/api/v1"
)

def run_orchestrator(topic):
    scriptwriter = Agent(
        role='Scriptwriter',
        goal='Write engaging short-form scripts tailored for ASMR and visual storytelling.',
        backstory='Expert viral content creator knowing exactly how to hook viewers.',
        verbose=True,
        memory=True,
        llm=openrouter_llm
    )

    visual_director = Agent(
        role='Visual Director',
        goal='Create precise visual prompts for Pollinations.ai matching the script.',
        backstory='Specialist in sensory-heavy, hypnotic visual aesthetics.',
        verbose=True,
        llm=openrouter_llm
    )

    task = Task(
        description=f'Generate a script and visual prompts for the topic: {topic}',
        expected_output='A structured JSON containing script sentences and image prompts.',
        agent=scriptwriter
    )

    crew = Crew(
        agents=[scriptwriter, visual_director],
        tasks=[task],
        process=Process.sequential,
        verbose=True
    )
    
    result = crew.kickoff()
    return result
