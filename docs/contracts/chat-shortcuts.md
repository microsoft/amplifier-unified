# Numbered chat navigation

Approved October 17, 2026 for the bundled web client (#29).

Hold **Alt+Shift** (Option+Shift on Mac) for about one second to reveal numbers
on the first nine currently rendered sidebar chats. While holding both keys,
press 1–9 to open a chat through the same action as its row. The snapshot follows
the displayed section/filter order and excludes hidden, collapsed and disabled
rows. Duplicate session IDs share one target. Background changes cannot change
the identity assigned to a number during a gesture. A removed target is a no-op.

Badges overlay the row without changing its geometry. Release either key, press
Escape, leave the window, focus an editor, or navigate to dismiss them. No shortcut
runs during text entry, IME composition, menus or dialogs. Unsaved-work and draft
guards remain owned by the existing navigation action. The shortcut reads only
the already rendered bounded sidebar, never fetching more history.

The web client leaves Command/Ctrl+number tab shortcuts and unarmed digit input
alone. Alt+Shift is a page shortcut, not an operating-system global binding;
system accessibility/input-source assignments may consume it. Pointer navigation
remains available. Chromium browser qualification is recorded separately from
native operating-system acceptance.
