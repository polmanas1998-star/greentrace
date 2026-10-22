"""The five agents: Extractor, Planner, Investigator, Evaluator, Writer.

Each one has a rule fallback, so the whole investigation runs without a model
(SAMPLE_MODE, or no provider answering); the trace then says "degraded".
"""
