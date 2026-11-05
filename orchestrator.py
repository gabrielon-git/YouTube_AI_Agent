import os
from crewai import Agent, Task, Crew, Process

# Safely grab the API key if it exists
api_key = os.environ.get("OPENROUTER_API_KEY")
if api_key:
    os.environ["OPENROUTER_API_KEY"] = api_key

def run_orchestrator(topic):
    scriptwriter = Agent(
        role='Scriptwriter',
        goal='Write engaging short-form scripts tailored for ASMR and visual storytelling.',
        backstory='Expert viral content creator knowing exactly how to hook viewers.',
        verbose=True,
        memory=True
    )

    visual_director = Agent(
        role='Visual Director',
        goal='Create precise visual prompts for Pollinations.ai matching the script.',
        backstory='Specialist in sensory-heavy, hypnotic visual aesthetics.',
        verbose=True
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
