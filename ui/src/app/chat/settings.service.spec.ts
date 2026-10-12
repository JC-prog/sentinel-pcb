import { SettingsService } from './settings.service';

describe('SettingsService', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('uses OpenAI, the only provider, and the server default model until one is picked', () => {
    const service = new SettingsService();
    expect(service.provider()).toBe('openai');
    expect(service.model()).toBeNull();
    expect(service.isOpen()).toBe(false);
  });

  it('opens and closes the settings modal', () => {
    const service = new SettingsService();
    service.open();
    expect(service.isOpen()).toBe(true);
    service.close();
    expect(service.isOpen()).toBe(false);
  });

  it('remembers the picked model across instances', () => {
    new SettingsService().setModel('gpt-4o');

    expect(new SettingsService().model()).toBe('gpt-4o');
  });

  it('drops a stored model the server no longer offers', () => {
    const service = new SettingsService();
    service.setModel('retired-model');

    service.setAvailableModels(['gpt-4o-mini', 'gpt-4o'], 'gpt-4o-mini');

    expect(service.model()).toBeNull();
    expect(new SettingsService().model()).toBeNull();
    expect(service.models()).toEqual(['gpt-4o-mini', 'gpt-4o']);
  });

  it('keeps a stored model the server still offers', () => {
    const service = new SettingsService();
    service.setModel('gpt-4o');

    service.setAvailableModels(['gpt-4o-mini', 'gpt-4o'], 'gpt-4o-mini');

    expect(service.model()).toBe('gpt-4o');
  });
});
