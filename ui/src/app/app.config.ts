import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter } from '@angular/router';
import { routes } from './app.routes';
import { CHAT_RESPONDER } from './chat-responder';
import { HttpChatResponder } from './http-chat-responder';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    { provide: CHAT_RESPONDER, useClass: HttpChatResponder },
  ]
};
