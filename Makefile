.PHONY: test run state
test:
	python3 -m unittest discover -v
run:
	python3 actions.py
state:
	python3 tools/nbstate.py
