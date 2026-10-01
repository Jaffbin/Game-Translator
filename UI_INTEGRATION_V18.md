# AutoGame Localizer UI Integration v18

## Scope
v18 is an editor-productivity pass on top of v17. No translation provider, project storage, installer, or patch engine was rewritten.

## UI changes
- Quick Actions under the Translation Editor:
  - QA this entry
  - Best TM
  - Apply glossary
  - Restore saved
- Bulk entry toolbar adds Recheck for selected entries.
- QA inline navigator adds Next QA.
- Ctrl+Shift+R rechecks the current entry's QA without running the project-wide QA task.
- Best TM replaces the current translation instead of appending at the cursor.
- Restore saved is protected by a discard-confirmation dialog.
- Saved translation snapshot is tracked locally for the currently selected Entry.

## Compatibility
- Preserves v17 Context / Glossary / Translation Memory / AI Suggestion / History / Update Sync.
- Preserves existing API contracts; v18 adds no new backend route.
- Existing phase_*.py modules remain in place.

## Tests
- pytest: 33 passed, 2 pre-existing Pydantic deprecation warnings.
- UI runtime tests: baseline + v15 + v16 + v17 + v18 all passed.
- 1000x700 and 1220x860 runtime viewport checks passed.
