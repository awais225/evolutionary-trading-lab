# Task prompt — HERO1 — GRAPHICAL THEME CONSISTENCY FIX (MANDATORY UI STANDARD)
Archived verbatim BEFORE implementation (2026-11-06), per the documentation protocol.

## Objective

Fix the visual inconsistency in the following three controls:

1. Global Instrument dropdown in the top bar.
2. Visual Builder / JSON Editor segmented control.
3. All Nodes / Starred Only segmented control, including the starred count badge.

They currently look flat, grey, white, or visually outdated compared with Hero1's existing graphical interface.

Do not merely change their colors. Bring them into alignment with the existing Hero1 graphical design system.

## 1. Inspect before modifying

Inspect the existing implementation and identify the actual styling patterns used by:

- Sidebar and active navigation items.
- Existing graphical buttons and button variants.
- Brand/logo treatments.
- Existing badges and status indicators.
- Theme tokens in `apps/web/src/styles/theme.css`.
- Shared components and interaction states.

Reuse existing design tokens and components wherever possible. Do not create an unrelated visual system or introduce arbitrary styling.

## 2. Fix the three controls

### A. Global Instrument dropdown

Replace the native HTML `<select>` with a properly themed custom dropdown/listbox.

Requirements:

- Preserve the existing instrument registry, availability information, selection persistence and global shared state.
- Preserve XAUUSD as the default when no saved selection exists.
- Preserve unavailable instruments as disabled options where currently supported.
- Use a themed trigger, chevron, dropdown panel, selected-item indicator and hover states.
- Support keyboard navigation: Arrow Up/Down, Enter, Escape and appropriate focus handling.
- Implement correct accessibility semantics, including `aria-expanded`, `aria-controls`, listbox and option roles where appropriate.
- Close the menu when an option is selected or the user clicks outside.
- Do not use a native OS dropdown popup.

### B. Visual Builder / JSON Editor

Restyle both segments to match Hero1's graphical buttons.

- Active segment: existing amber gradient treatment, subtle amber glow, appropriate border and dark readable text.
- Inactive segment: themed text and background, with a visible graphical hover state.
- Include hover, focus-visible, active and disabled states.
- Preserve existing switching behavior and state.

### C. All Nodes / Starred Only

Apply the same shared segmented-control styling, scaled appropriately for its smaller size.

- Match the Visual Builder / JSON Editor control.
- Style the starred count as a themed amber-soft badge.
- Preserve filtering, selection and count behavior.

## 3. Permanent design-system rule

This is not a one-time styling fix.

From now on, EVERY new or modified UI element in Hero1 must follow the existing graphical theme.

This applies to:

- Buttons and segmented controls.
- Dropdowns, selects and listboxes.
- Inputs, textareas and search fields.
- Checkboxes, radio buttons and switches.
- Tabs, filters and toolbars.
- Badges, counters and status indicators.
- Tables, menus, dialogs and popovers.
- Empty states, loading states and error states.
- Any new controls added to any of the eight application tabs.

Mandatory rules:

1. Inspect and reuse the existing shared components, theme tokens and styling conventions before creating a new control.
2. Never introduce generic grey/white buttons or default browser/OS styling.
3. Never use unstyled native controls when the existing design system provides a themed equivalent.
4. Avoid arbitrary hex colors, shadows, gradients, radii, fonts and spacing.
5. Ensure every interactive component has consistent default, hover, active, focus-visible, disabled and selected states where applicable.
6. Maintain sufficient contrast and accessibility.
7. Keep all eight tabs visually consistent.
8. Do not change the established Hero1 theme or redesign unrelated components.
9. If a required component does not exist, create a reusable themed component and use it consistently rather than implementing isolated one-off styling.
10. Update the relevant shared theme/component documentation so future implementation work follows this standard.

## 4. Testing and visual verification

Do not stop after changing CSS.

- Run frontend tests and production build.
- Add or update tests for custom dropdown keyboard interaction, selection and persistence.
- Inspect the actual rendered application.
- Verify all three controls in both their default and interactive states.
- Verify the dropdown does not display an OS-native white/grey popup.
- Check the appearance in the existing application theme.
- Check that no unrelated UI or functionality has regressed.

If workspace preview is available, launch the actual Hero1 frontend and inspect it there. Do not claim visual verification based only on CSS inspection.

## 5. Scope restrictions

- Do not change backend logic.
- Do not change instrument registry or trading behavior.
- Do not change node data, strategy hashes or persistence semantics.
- Do not modify unrelated tabs or redesign the application.
- Do not overwrite unrelated user changes.
- Keep the change focused on UI styling and the dropdown component.

## 6. Deliverables

At completion, report:

1. Files changed.
2. Shared theme components/tokens reused.
3. How the three controls now look and behave.
4. Tests and production build results.
5. Whether actual browser/workspace visual verification was completed.
6. Any remaining limitations.
7. Commit SHA and confirmation that changes were pushed to the existing Hero1 repository, if authorized and successful.

**Acceptance criterion:** These controls must visually belong to the same graphical interface as the existing Hero1 sidebar, buttons and badges. No generic grey/white controls or OS-native dropdown popup should remain in these three areas.
