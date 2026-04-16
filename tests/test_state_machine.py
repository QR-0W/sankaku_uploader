from sankaku_uploader.domain import ItemStatus, TaskStatus, can_transition_item, can_transition_task


def test_task_state_transitions() -> None:
    assert can_transition_task(TaskStatus.PENDING, TaskStatus.RUNNING)
    assert can_transition_task(TaskStatus.RUNNING, TaskStatus.PAUSED)
    assert not can_transition_task(TaskStatus.COMPLETED, TaskStatus.RUNNING)


def test_item_state_transitions() -> None:
    assert can_transition_item(ItemStatus.PENDING, ItemStatus.UPLOADING)
    assert can_transition_item(ItemStatus.WAITING_USER_CONFIRM, ItemStatus.SUBMITTING)
    assert not can_transition_item(ItemStatus.SUCCESS, ItemStatus.PENDING)
