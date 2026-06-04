The Development of Larger Features

For larger features, that is one comprising multiple PRs, we will do a composition of the "HowTo: Executing Simple Tasks" .
The key aspect here is that there is a corresponding "over all" agent, branch and PR for the feature, but its execution is composed of a series of PRs (these in tandem with HowTo: Executing Simple Tasks")

1. Information Gathering: A supervising agent is started , and like before, given a task via gh issue, a product spec or a chat with the user.
    It will do a : 
    1.1 general reading / research,
    1.2 Create a feature branch for the work
    1.3 Ask users for decisions / clarifications as needed.
2. Execution: 
    The key thing is the coordinating agent should not handle the implementation. Its role is to keep the execution of various smaller parts correct, cohesive and on track. If it does the implementation itself, it will consume its context on that front, and either worsen its own performance as the context window moves from good to bad context, or require compaction. Additionally, this keeps the token usage smaller (good for cost.)
    The agent will spin subagents assigning them their own part (ideally from a gh issue for that). Each subagent will handle the full single-task cycle for its part (implement, then shepherd its own PR through review and CI to Ready), reporting back to the coordinating agent, which integrates the parts and keeps the overall feature on track.
