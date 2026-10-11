# Settings ownership

Global Settings contains connection preferences, app appearance, workspace defaults
and starters, voice defaults, notifications, global file permissions, host screen
setup, diagnostic capture, saved-work import/cleanup, app/component updates,
installed bundles and registries, tool connections, runtime readiness policy,
backup/repair, reset, and terminal/app installation help.

Controls for an existing conversation are in Chat:

| Controls | Destination |
| --- | --- |
| Name, identity, workspace, saved history | Chat info |
| Providers, tools, loaded modules, save/share bundle | Advanced → Model & tools |
| Context/recovery and recorded errors | Advanced → Context & recovery |
| Screen source, consent, worker computer tools | Advanced → Computer access |
| Memory and past-work preferences | Advanced → Memory & past work |
| Project/local file permissions | Advanced → Project file access |
| Transcript export and sharing | Share or save |
| Current outputs and publishing | Canvas → Chat overview |
| Plan | Chat overview, with optional inline/bottom placement |

Old global deep links route to those destinations. They no longer appear as empty
redirect pages. Global host screen setup excludes the selected conversation's
screen source and mounted worker tools. The Updates page's older-source inventory
is host-wide update evidence across saved conversations, not a current-chat editor.

Advanced configuration editors retain explicit scope selection where a shared
setting can be overridden by project/local configuration. Those are labelled scope
choices, not controls that act silently on the selected chat. Global file access
and project file access have separate surfaces and drafts.

Settings in Canvas uses the same native settings components and typed actions as
the standalone dialog. Agents inspect `settingsUI` for routes and `visibleUI` for
rendered controls; private credential fields are not shared in either. Assisted
setup is optional once a chat model is configured. The user chooses demonstrations;
there is no automatic paid image, voice, or external action in the tour.
