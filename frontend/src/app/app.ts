import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

/** The root: `/login` renders bare, every other route inside the `Shell` (see app.routes.ts). */
@Component({
  imports: [RouterOutlet],
  selector: 'app-root',
  template: '<router-outlet />',
})
export class App {}
