import { FormControl, FormGroup, Validators } from '@angular/forms';
import { clearServerErrors } from './form-errors';

describe('clearServerErrors', () => {
  it('drops a server error from a valid control', () => {
    const form = new FormGroup({ name: new FormControl('Autumn Cup') });
    form.controls.name.setErrors({ server: 'Name taken.' });
    clearServerErrors(form);
    expect(form.controls.name.errors).toBeNull();
    expect(form.valid).toBe(true);
  });

  it("keeps the control's own validator errors (required, min) after a server error", () => {
    const form = new FormGroup({
      name: new FormControl('', Validators.required),
      count: new FormControl(0, Validators.min(1)),
    });
    form.controls.name.setErrors({ server: 'Bad name.' });
    form.controls.count.setErrors({ server: 'Bad count.' });
    clearServerErrors(form);
    expect(form.controls.name.errors).toEqual({ required: true });
    expect(form.controls.count.hasError('min')).toBe(true);
    expect(form.controls.count.hasError('server')).toBe(false);
    expect(form.invalid).toBe(true);
  });

  it('leaves a control without a server error alone', () => {
    const form = new FormGroup({ name: new FormControl('', Validators.required) });
    form.controls.name.setErrors({ custom: true });
    clearServerErrors(form);
    expect(form.controls.name.errors).toEqual({ custom: true });
  });
});
