import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { AuthService } from '../../auth.service';
import { SettingsService } from '../settings.service';
import { Settings } from './settings';

function fakeAuth(response: Partial<Response> | Error): AuthService {
  return {
    fetchWithAuth: vi.fn(async () => {
      if (response instanceof Error) {
        throw response;
      }
      return response as Response;
    }),
  } as unknown as AuthService;
}

const MODELS = { default: 'gpt-4o-mini', models: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1'] };

async function create(auth: AuthService): Promise<{
  fixture: ComponentFixture<Settings>;
  settingsService: SettingsService;
}> {
  await TestBed.configureTestingModule({
    imports: [Settings],
    providers: [{ provide: AuthService, useValue: auth }],
  }).compileComponents();
  const settingsService = TestBed.inject(SettingsService);
  const fixture = TestBed.createComponent(Settings);
  await fixture.componentInstance.ngOnInit(); // the model list is fetched here
  fixture.detectChanges();
  return { fixture, settingsService };
}

describe('Settings', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('lists the models the server offers, with the default marked and selected', async () => {
    const { fixture } = await create(fakeAuth({ ok: true, json: async () => MODELS }));
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';

    for (const model of MODELS.models) {
      expect(text).toContain(model);
    }
    expect(text).toContain('default');
    const radios = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('input[type="radio"]'),
    ) as HTMLInputElement[];
    expect(radios.map((r) => r.checked)).toEqual([true, false, false]);
  });

  it('remembers a picked model, and picking the default clears the choice', async () => {
    const { fixture, settingsService } = await create(
      fakeAuth({ ok: true, json: async () => MODELS }),
    );
    const radios = () =>
      Array.from(
        (fixture.nativeElement as HTMLElement).querySelectorAll('input[type="radio"]'),
      ) as HTMLInputElement[];

    radios()[1].click();
    fixture.detectChanges();
    expect(settingsService.model()).toBe('gpt-4o');
    expect(radios().map((r) => r.checked)).toEqual([false, true, false]);

    radios()[0].click();
    fixture.detectChanges();
    expect(settingsService.model()).toBeNull();
  });

  it('says the default is used when the list cannot be loaded', async () => {
    const { fixture } = await create(fakeAuth(new Error('offline')));

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('server default is used');
  });

  it('closes when the close button is clicked', async () => {
    const { fixture, settingsService } = await create(
      fakeAuth({ ok: true, json: async () => MODELS }),
    );
    settingsService.open();
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('button[aria-label="Close settings"]') as HTMLElement).click();

    expect(settingsService.isOpen()).toBe(false);
  });
});
