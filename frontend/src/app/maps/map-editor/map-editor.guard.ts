import { CanDeactivateFn } from '@angular/router';
import { MapEditor } from './map-editor';

/** Leaving the editor with unsaved edits asks first (the shared confirm dialog). */
export const confirmLeaveMapEditor: CanDeactivateFn<MapEditor> = (editor) =>
  editor.confirmDiscard('Leaving the editor');
