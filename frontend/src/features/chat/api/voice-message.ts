import { uploadAttachment, toMessageAttachment, type ApiAttachment } from './chat-api';
import { newClientMessageId } from './chat-socket';
import type { Message, MessageReply, User } from '../types';
import { VoiceRecorderError, type VoiceRecorderStage } from '../voice-recorder';

interface VoiceMessageSocket {
  readonly readyState: number;
  send(data: string): void;
}

interface VoiceMessageDependencies {
  upload: typeof uploadAttachment;
  createClientMessageId: typeof newClientMessageId;
  now: () => Date;
}

interface UploadAndSendVoiceMessageOptions {
  token: string;
  conversationId: string;
  file: File;
  replyTo?: MessageReply;
  socket: VoiceMessageSocket;
  sender: Pick<User, 'id' | 'name' | 'avatar'>;
  onStage: (stage: Extract<VoiceRecorderStage, 'uploading' | 'sending'>) => void;
  onOptimistic: (message: Message) => void;
  onOptimisticRejected: (clientMessageId: string) => void;
}

const DEFAULT_DEPENDENCIES: VoiceMessageDependencies = {
  upload: uploadAttachment,
  createClientMessageId: newClientMessageId,
  now: () => new Date(),
};

export async function uploadAndSendVoiceMessage(
  options: UploadAndSendVoiceMessageOptions,
  dependencies: Partial<VoiceMessageDependencies> = {},
): Promise<string> {
  const resolved = { ...DEFAULT_DEPENDENCIES, ...dependencies };
  if (options.socket.readyState !== WebSocket.OPEN) {
    throw new VoiceRecorderError('voice_send_failed');
  }

  options.onStage('uploading');
  let uploaded: ApiAttachment;
  try {
    uploaded = await resolved.upload(options.token, options.conversationId, options.file);
  } catch {
    throw new VoiceRecorderError('voice_upload_failed');
  }

  options.onStage('sending');
  if (options.socket.readyState !== WebSocket.OPEN) {
    throw new VoiceRecorderError('voice_send_failed');
  }

  const clientMessageId = resolved.createClientMessageId();
  const createdAt = resolved.now();
  options.onOptimistic({
    id: clientMessageId,
    clientMessageId,
    senderId: options.sender.id,
    senderName: options.sender.name,
    senderAvatar: options.sender.avatar,
    conversationId: options.conversationId,
    content: '',
    messageType: 'voice',
    transcriptionStatus: 'pending',
    timestamp: 'Now',
    createdAt: createdAt.toISOString(),
    status: 'sending',
    attachments: [toMessageAttachment(uploaded)],
    replyTo: options.replyTo,
  });

  try {
    options.socket.send(JSON.stringify({
      type: 'send_voice_message',
      client_message_id: clientMessageId,
      conversation_id: options.conversationId,
      attachment_id: uploaded.id,
      reply_to_message_id: options.replyTo?.id ?? null,
    }));
  } catch {
    options.onOptimisticRejected(clientMessageId);
    throw new VoiceRecorderError('voice_send_failed');
  }

  return clientMessageId;
}
