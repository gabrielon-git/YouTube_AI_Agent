import os
from crewai import Agent, Task, Crew, Process
from browser_use import Browser

# Grab API keys from environment variables set on Render
os.environ["OPENROUTER_API_KEY"] = os.environ.get("OPENROUTER_API_KEY")

# 1. Define your Agents
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

# 2. Define Tasks
def create_video_task(topic):
    return Task(
        description=f'Generate a script and visual prompts for the topic: {topic}',
        expected_output='A structured JSON containing script sentences and image prompts.',
        agent=scriptwriter
    )

# 3. Main Orchestration Function
def run_orchestrator(topic):
    crew = Crew(
        agents=[scriptwriter, visual_director],
        tasks=[create_video_task(topic)],
        process=Process.sequential,
        verbose=True
    )
    result = crew.kickoff()
    return result
