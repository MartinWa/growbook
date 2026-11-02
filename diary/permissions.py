"""Diary write authority: the author or an active staff administrator."""


def can_edit_entry(user, entry):
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and (user.is_staff or entry.author_id == user.pk)
    )
