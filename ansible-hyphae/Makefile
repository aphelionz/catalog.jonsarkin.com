ANSIBLE ?= ansible-playbook
INVENTORY ?= inventory
PLAY ?= playbooks/site.yml
EXTRA_ARGS ?=
ANSIBLE_LINT ?= ansible-lint

.PHONY: hello run lint

hello:
	$(ANSIBLE) -i $(INVENTORY) $(EXTRA_ARGS) playbooks/site.yml

run:
	$(ANSIBLE) -i $(INVENTORY) $(EXTRA_ARGS) $(PLAY)

lint:
	$(ANSIBLE_LINT)
