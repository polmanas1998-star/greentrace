"""The court of inquiry: an adversarial debate layer on top of the five agents; the verdict stays with the rules.

Plaintiff (app/court/plaintiff.py) argues the supplier's promise from the pieces the run already
read. Defense is the Evaluator: its refutations R1-R9, each with its own query and rows, become
the counter-arguments (app/court/defense.py). Judge is app/rules.py; the Writer words the
judgment (app/court/judge.py). Nothing is read again: the court works on the finished run, its
trace and its World Model. GET /runs/{id}/court and GET /runs/{id}/attacks: app/court/routes.py.
"""
