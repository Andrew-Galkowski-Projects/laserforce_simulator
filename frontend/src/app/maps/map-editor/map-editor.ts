import { Component } from '@angular/core';
import { MatAnchor } from '@angular/material/button';
import { RouterLink } from '@angular/router';

/**
 * `/maps/:id/editor`: a placeholder until the editor foundation ticket (ANG-06h-2) replaces it
 * with the editor shell. The map list's Open Editor and a successful upload land here.
 */
@Component({
  selector: 'app-map-editor',
  imports: [MatAnchor, RouterLink],
  template: `
    <h1>Map Editor</h1>
    <p>The map editor is coming soon.</p>
    <a mat-stroked-button routerLink="/maps">Back to Maps</a>
  `,
})
export class MapEditor {}
