import { rendererPolicy } from "./runtime-policy.generated";

export const MAX_MESSAGE_CHARACTERS = rendererPolicy.max_message_characters;

export function messageCharacterCount(value: string): number {
  return Array.from(value.trim()).length;
}

export function messageValidationError(value: string): string | null {
  const count = messageCharacterCount(value);
  if (count <= MAX_MESSAGE_CHARACTERS) return null;
  return `Message exceeds the ${MAX_MESSAGE_CHARACTERS.toLocaleString("en-US")}-character limit by ${(count - MAX_MESSAGE_CHARACTERS).toLocaleString("en-US")} characters. Shorten it or split it into separate messages. Your draft is saved.`;
}
