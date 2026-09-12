"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { WifiOff } from "lucide-react";
import { shellChromeText } from "@/shared/i18n/shell";
import { clearSession, getAccessToken, getRefreshToken } from "@/shared/lib/session";
import { signOut, updateInterfaceLanguage, updatePreferredLanguage } from "@/shared/api/account-api";
import type { AppSettings, Conversation, Message, MessageAttachment, MessageMention, SidebarTab, ToastItem, User } from "../types";
import { DEFAULT_CHAT_SETTINGS } from "../constants";
import {
  acceptCall,
  addGroupMembers,
  addReaction,
  ASSISTANT_AVATAR_URL,
  ASSISTANT_CONVERSATION_TITLE,
  blockContact,
  compareConversations,
  createConversation,
  ChatApiError,
  createCalendarEvent,
  deleteGroup,
  deleteMessage,
  downloadAttachment,
  getAttachmentDirectUrl,
  endCall,
  fetchAttachmentBlob,
  fetchTTSAudio,
  fetchTTSText,
  getAgentConsents,
  getAssistantConversation,
  getMe,
  getMessages,
  getUserSettings,
  joinCall,
  leaveConversation,
  leaveGroup,
  listActionProposals,
  listAttachments,
  listConversations,
  listUsers,
  confirmActionProposal,
  rejectActionProposal,
  markRead,
  rejectCall,
  removeGroupMember,
  removeReaction,
  retryTranslation,
  retryVoiceTranscription,
  saveMessage,
  scanConversationForActionProposals,
  formatRelativeMessageTime,
  searchMessages,
  startCall as startRtcCall,
  submitTranslationEdit,
  submitTranslationFeedback,
  toChatUser,
  toConversation,
  toApiMessageFromRealtime,
  toLanguageCode,
  toMessage,
  toMessageAttachment,
  toMessages,
  transferGroupOwnership,
  unsaveMessage,
  updateConversationPreferences,
  updateGroupDetails,
  updateAgentConsents,
  updateGroupRole,
  updateProfile,
  updateUserSettings,
  uploadAttachment,
  type AgentConsentScope,
  type ApiActionProposal,
  type ApiAgentConsents,
  type ApiCall,
  type ApiConversation,
  type ApiMessageReaction,
  type ApiRealtimeMessage,
  type ApiUserSettings,
} from "../api/chat-api";
import { newClientMessageId, socketUrl } from "../api/chat-socket";
import { uploadAndSendVoiceMessage } from "../api/voice-message";
import {
  applyTranslationCompleted,
  applyVoiceTranscriptionCompleted,
  applyVoiceTranscriptionFailed,
  applyVoiceTranscriptionRetryAccepted,
  reconcileRealtimeMessage,
  type VoiceTranscriptionCompletedPayload,
  type VoiceTranscriptionFailedPayload,
} from "../message-events";
import { VoiceRecorderError, type VoiceRecorderStage } from "../voice-recorder";
import { VoiceStatusRefreshScheduler } from "../voice-status-refresh";
import { interactionText, tx } from "../i18n";
import { MiniSidebar } from "./MiniSidebar";
import { ConversationPanel } from "./ConversationPanel";
import { ContactsPanel } from "./ContactsPanel";
import { GroupsPanel } from "./GroupsPanel";
import { ChatView } from "./ChatView";
import { NewConversationModal } from "./NewConversationModal";
import { isAwaitingDecision } from "../proposal-approval";
import { CreateGroupModal } from "./CreateGroupModal";
import { SettingsModal, type SettingsSection } from "./SettingsModal";
import { TaskInboxPanel } from "./TaskInboxPanel";
import { CallModal, type ActiveCall } from "./CallModal";
import { ToastContainer } from "./ToastContainer";
import { ForwardMessageModal } from "./ForwardMessageModal";
import { PersonalCalendar } from "./PersonalCalendar";
import type { ConversationSearchResult } from "./MessageSearchPanel";
import { useTTS } from "../hooks/useTTS";

const EMPTY_USER: User = { id: "", email: "", name: "", username: "", avatar: "", nativeLanguage: "en", onlineStatus: "offline" };

function conversationUsers(items: Awaited<ReturnType<typeof listConversations>>): User[] {
  const indexed = new Map<string, User>();
  for (const item of items) for (const member of item.members) indexed.set(member.id, toChatUser(member));
  return [...indexed.values()];
}

function isAssistantConversation(item: ApiConversation): boolean {
  return item.title === ASSISTANT_CONVERSATION_TITLE;
}

function toAssistantConversation(
  item: ApiConversation,
  currentUserId: string,
  interfaceLanguage: User["nativeLanguage"],
): Conversation {
  return {
    ...toConversation(item, currentUserId, interfaceLanguage),
    type: "direct",
    name: "Trợ lý thông minh",
    avatar: ASSISTANT_AVATAR_URL,
    recipient: undefined,
    members: undefined,
    memberCount: undefined,
    description: "Không gian riêng tư của bạn",
  };
}

function sortConversations(items: Conversation[]): Conversation[] {
  return [...items].sort(compareConversations);
}

function keepConversationIdentityWhenUnchanged(
  previous: Conversation[],
  next: Conversation[],
): Conversation[] {
  return JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
}

function toActiveCall(call: ApiCall, phase: ActiveCall["phase"]): ActiveCall {
  return {
    id: call.call_id,
    conversationId: call.conversation_id,
    callerId: call.caller_id,
    calleeId: call.callee_id,
    type: call.call_type,
    phase,
    roomUrl: call.room_url ?? undefined,
    joinToken: call.join_token ?? undefined,
  };
}

function persistedSettings(settings: ApiUserSettings): Pick<AppSettings, "autoTranslate" | "showOriginalByDefault" | "translationTone" | "soundEnabled" | "readReceipts" | "aiSmartAssistance"> {
  return {
    autoTranslate: settings.auto_translate,
    showOriginalByDefault: settings.show_original_by_default,
    translationTone: settings.translation_tone,
    soundEnabled: settings.sound_enabled,
    readReceipts: settings.read_receipts,
    aiSmartAssistance: settings.ai_smart_assistance,
  };
}

/** Flatten the consent list into the lookup the settings switches read.
 *
 *  The server always sends every scope, so a scope missing from this map means
 *  the response was malformed rather than that the permission is unknown — and
 *  an absent key reads as "not granted", which is the safe way to be wrong.
 */
function toConsentMap(response: ApiAgentConsents): Partial<Record<AgentConsentScope, boolean>> {
  return Object.fromEntries(response.consents.map((entry) => [entry.scope, entry.is_granted]));
}

/** Whether every permission carries an explicit decision.
 *
 *  Not the same as "any are granted". The server sends all five scopes whether
 *  or not the user has seen them, so presence proves nothing; a scope with
 *  neither timestamp is one nobody has been asked about. Refusing is an answer,
 *  and re-prompting someone who refused teaches them to dismiss the dialog.
 */
function consentsAllAnswered(response: ApiAgentConsents): boolean {
  return response.consents.every((entry) => entry.granted_at !== null || entry.revoked_at !== null);
}

function applyDisplayPreference(messages: Message[], showOriginalByDefault: boolean): Message[] {
  return messages.map((message) => message.translation
    ? { ...message, translation: { ...message.translation, showOriginal: message.translation.showOriginal ?? showOriginalByDefault } }
    : message);
}

function toReactions(reactions: ApiMessageReaction[]) {
  return reactions.map((reaction) => ({ emoji: reaction.emoji, count: reaction.count, users: reaction.user_ids }));
}
export const AppShell: React.FC = () => {
  const router = useRouter();
  const tts = useTTS();
  const socket = useRef<WebSocket | null>(null);
  // A WebSocket reaches OPEN before the application-level auth handshake has
  // completed.  Sending in that interval makes the server interpret the first
  // payload as an invalid authentication event, while the optimistic bubble
  // remains in the browser only.  Keep this separate from readyState so a
  // message is never presented as sent until the socket has received auth_ok.
  const socketAuthenticated = useRef(false);
  const token = useRef<string | null>(null);
  const [currentUser, setCurrentUser] = useState<User>(EMPTY_USER);
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_CHAT_SETTINGS);
  // Empty until loaded, and an absent scope reads as not granted — the same
  // closed default the backend applies when no row exists.
  const [agentConsents, setAgentConsents] = useState<Partial<Record<AgentConsentScope, boolean>>>({});
  const [agentConsentsAnswered, setAgentConsentsAnswered] = useState(true);
  // Mirrors the `token` ref. The ref is right for handlers, but a pane
  // rendered from `token.current` would never re-render when the token
  // arrives asynchronously — it would just stay empty.
  const [accessToken, setAccessToken] = useState<string | null>(null);
  // Read inside the socket handler. A ref rather than the setting itself
  // because the socket effect already re-runs on its dependencies, and
  // adding a cosmetic preference to that list would tear down and rebuild
  // the WebSocket every time somebody toggled sound.
  const soundEnabledRef = useRef(true);
  const [pendingTaskCount, setPendingTaskCount] = useState(0);
  const [incomingProposals, setIncomingProposals] = useState<ApiActionProposal[]>([]);
  const [proposalBusyId, setProposalBusyId] = useState<string | null>(null);
  // A counter rather than a boolean: two calendar changes in a row must
  // both trigger a reload, and a boolean flipped twice reads as unchanged.
  const [calendarRefreshCount, setCalendarRefreshCount] = useState(0);
  const [users, setUsers] = useState<User[]>([]);
  // null means no search has run, so show contacts. An array contains server search results.
  const [userSearchResults, setUserSearchResults] = useState<User[] | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messagesMap, setMessagesMap] = useState<Record<string, Message[]>>({});
  const [attachmentsMap, setAttachmentsMap] = useState<Record<string, MessageAttachment[]>>({});
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<SidebarTab>("chats");
  const [mobileView, setMobileView] = useState<"list" | "chat">("list");
  const [isNewChatOpen, setIsNewChatOpen] = useState(false);
  const [isCreateGroupOpen, setIsCreateGroupOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [settingsInitialSection, setSettingsInitialSection] = useState<SettingsSection>("language");
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [activeCall, setActiveCall] = useState<ActiveCall | null>(null);
  const [forwardingMessage, setForwardingMessage] = useState<Message | null>(null);
  const [isAssistantChatOpen, setIsAssistantChatOpen] = useState(false);
  const [assistantConversation, setAssistantConversation] = useState<Conversation | null>(null);
  const [retryingTranscriptionIds, setRetryingTranscriptionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const retryingTranscriptionIdsRef = useRef<Set<string>>(new Set());
  const voiceStatusRefreshSchedulerRef = useRef<VoiceStatusRefreshScheduler | null>(null);
  if (voiceStatusRefreshSchedulerRef.current === null) {
    voiceStatusRefreshSchedulerRef.current = new VoiceStatusRefreshScheduler();
  }
  const selectedConversationIdRef = useRef<string | null>(null);

  useEffect(() => {
    selectedConversationIdRef.current = selectedConversationId;
  }, [selectedConversationId]);

  useEffect(() => () => voiceStatusRefreshSchedulerRef.current?.clear(), []);

  const addToast = useCallback((title: string, message?: string, type: ToastItem["type"] = "info") => {
    const id = `toast_${Date.now()}`;
    setToasts((items) => [...items, { id, title, message, type }]);
    window.setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), 3500);
  }, []);

  const handleCallMediaError = useCallback((message: string) => {
    addToast("Cuộc gọi", message, "warning");
  }, [addToast]);

  const handleLogout = useCallback(async () => {
    if (isLoggingOut) return;
    setIsLoggingOut(true);
    const refreshToken = getRefreshToken();

    try {
      if (refreshToken) await signOut(refreshToken);
    } catch {
      // Local logout must still complete when the network is unavailable.
    } finally {
      socket.current?.close();
      socket.current = null;
      token.current = null;
      setAccessToken(null);
      clearSession();
      router.replace("/login");
    }
  }, [isLoggingOut, router]);

  const selectedConversation = conversations.find((item) => item.id === selectedConversationId) ?? null;
  const activeConversation = isAssistantChatOpen ? assistantConversation : selectedConversation;
  const activeConversationId = activeConversation?.id ?? null;
  // Only the undecided ones, and only for the thread on screen. A proposal
  // already approved from the inbox must not reappear here asking again.
  const currentProposals = activeConversationId
    ? incomingProposals.filter(
        (proposal) => proposal.conversation_id === activeConversationId && isAwaitingDecision(proposal),
      )
    : [];
  const currentMessages = activeConversationId ? messagesMap[activeConversationId] ?? [] : [];
  const currentAttachments = activeConversationId ? attachmentsMap[activeConversationId] ?? [] : [];
  const usersById = useMemo(() => new Map([currentUser, ...users].filter((user) => user.id).map((user) => [user.id, user])), [currentUser, users]);
  const translationContextForConversation = useCallback((conversationId: string) => {
    const conversation = conversations.find((item) => item.id === conversationId);
    return {
      currentUserId: currentUser.id,
      isDirect: conversation?.type === 'direct',
      recipientLanguage: conversation?.recipient?.nativeLanguage,
    };
  }, [conversations, currentUser.id]);

  const initiateCall = async (type: "voice" | "video") => {
    if (!token.current || !selectedConversation) return;
    if (selectedConversation.type !== "direct") {
      addToast("Cuộc gọi chưa khả dụng", "Hiện chỉ hỗ trợ gọi 1–1.", "warning");
      return;
    }
    try {
      const call = await startRtcCall(token.current, selectedConversation.id, type);
      setActiveCall(toActiveCall(call, "outgoing"));
    } catch (error) {
      addToast("Không thể gọi", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const acceptIncomingCall = async () => {
    if (!token.current || !activeCall) return;
    try {
      const call = await acceptCall(token.current, activeCall.id);
      setActiveCall(toActiveCall(call, "active"));
    } catch (error) {
      addToast("Không thể nhận cuộc gọi", error instanceof Error ? error.message : undefined, "warning");
      setActiveCall(null);
    }
  };

  const rejectIncomingCall = async () => {
    if (!token.current || !activeCall) return;
    try {
      await rejectCall(token.current, activeCall.id);
    } catch (error) {
      addToast("Không thể từ chối cuộc gọi", error instanceof Error ? error.message : undefined, "warning");
    } finally {
      setActiveCall(null);
    }
  };

  const endCurrentCall = async () => {
    if (!token.current || !activeCall) return;
    try {
      await endCall(token.current, activeCall.id);
    } catch (error) {
      addToast("Không thể kết thúc cuộc gọi", error instanceof Error ? error.message : undefined, "warning");
    } finally {
      setActiveCall(null);
    }
  };

  const loadConversationMessages = useCallback(async (conversationId: string) => {
    if (!token.current) return;
    const history = await getMessages(token.current, conversationId);
    setMessagesMap((previous) => ({
      ...previous,
      [conversationId]: applyDisplayPreference(
        toMessages(history, usersById, settings.preferredLanguage, translationContextForConversation(conversationId)),
        settings.showOriginalByDefault,
      ),
    }));
  }, [settings.preferredLanguage, settings.showOriginalByDefault, translationContextForConversation, usersById]);

  const loadConversationAttachments = useCallback(async (conversationId: string) => {
    if (!token.current) return;
    try {
      const attachments = await listAttachments(token.current, conversationId);
      setAttachmentsMap((previous) => ({
        ...previous,
        [conversationId]: attachments.map(toMessageAttachment),
      }));
    } catch {
      // Keep chat usable during a rolling deployment where the frontend may
      // reach an older backend before the attachment-list route is available.
      setAttachmentsMap((previous) => ({ ...previous, [conversationId]: [] }));
    }
  }, []);

  const refreshConversations = useCallback(async () => {
    if (!token.current || !currentUser.id) return;
    const result = await listConversations(token.current);
    const visible = result.filter((item) => !isAssistantConversation(item));
    const mapped = sortConversations(
      visible.map((item) => toConversation(item, currentUser.id, settings.interfaceLanguage)),
    );
    setConversations((previous) => keepConversationIdentityWhenUnchanged(previous, mapped));
    const nextUsers = conversationUsers(visible).filter((user) => user.id !== currentUser.id);
    setUsers((previous) => JSON.stringify(previous) === JSON.stringify(nextUsers) ? previous : nextUsers);
  }, [currentUser.id, settings.interfaceLanguage]);

  const cancelVoiceStatusRefresh = useCallback((messageId: string) => {
    voiceStatusRefreshSchedulerRef.current?.cancel(messageId);
  }, []);

  const scheduleVoiceStatusRefresh = useCallback((messageId: string, conversationId: string) => {
    voiceStatusRefreshSchedulerRef.current?.schedule(
      messageId,
      conversationId,
      async (targetConversationId) => {
        await Promise.all([
          loadConversationMessages(targetConversationId),
          refreshConversations(),
        ]);
      },
    );
  }, [loadConversationMessages, refreshConversations]);

  useEffect(() => {
    const accessToken = getAccessToken();
    if (!accessToken) { router.replace("/login"); return; }
    token.current = accessToken;
    setAccessToken(accessToken);
    let active = true;
    getMe(accessToken).then(async (profile) => {
      if (profile.role?.toLowerCase() === "admin") {
        router.replace("/admin");
        return;
      }
      const user = toChatUser(profile);
      const [list, savedSettings, consents, proposals] = await Promise.all([
        listConversations(accessToken),
        getUserSettings(accessToken),
        // Never fatal: permissions default to closed, so failing to read them
        // shows every switch off rather than blocking the whole app from loading.
        getAgentConsents(accessToken).catch(() => null),
        // Approval controls must survive a browser reload, not only a live
        // websocket notification that happened during this tab's lifetime.
        listActionProposals(accessToken).catch(() => []),
      ]);
      if (!active) return;
      if (consents) {
        setAgentConsents(toConsentMap(consents));
        setAgentConsentsAnswered(consentsAllAnswered(consents));
      }
      const visible = list.filter((item) => !isAssistantConversation(item));
      const contacts = conversationUsers(visible).filter((contact) => contact.id !== user.id);
      setCurrentUser(user);
      setUsers(contacts);
      setIncomingProposals(proposals);
      const restoredSettings = {
        ...persistedSettings(savedSettings),
        preferredLanguage: user.nativeLanguage,
        interfaceLanguage: toLanguageCode(profile.interface_language),
      };
      setSettings((value) => ({ ...value, ...restoredSettings }));
      setConversations(sortConversations(
        visible.map((item) => toConversation(item, user.id, restoredSettings.interfaceLanguage)),
      ));
      if (visible[0]) {
        const firstConversation = toConversation(
          visible[0],
          user.id,
          restoredSettings.interfaceLanguage,
        );
        const members = new Map([user, ...contacts].map((contact) => [contact.id, contact]));
        const [history, attachments] = await Promise.all([
          getMessages(accessToken, visible[0].id),
          listAttachments(accessToken, visible[0].id).catch(() => []),
        ]);
        if (!active) return;
        setMessagesMap({
          [visible[0].id]: applyDisplayPreference(
            toMessages(history, members, user.nativeLanguage, {
              currentUserId: user.id,
              isDirect: firstConversation.type === 'direct',
              recipientLanguage: firstConversation.recipient?.nativeLanguage,
            }),
            restoredSettings.showOriginalByDefault,
          ),
        });
        setAttachmentsMap({ [visible[0].id]: attachments.map(toMessageAttachment) });
        setSelectedConversationId(visible[0].id);
      }
    }).catch(() => { clearSession(); router.replace("/login"); });
    return () => { active = false; };
  }, [router]);

  useEffect(() => {
    if (!currentUser.id || !token.current) return;
    let disposed = false;
    let retry: number | undefined;
    const connect = () => {
      socketAuthenticated.current = false;
      const ws = new WebSocket(socketUrl());
      socket.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: token.current }));
      ws.onmessage = (event) => {
        const payload = JSON.parse(event.data) as Record<string, unknown>;
        const eventType = payload.type as string;
        if (eventType === "ping") {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "pong" }));
          return;
        }
        if (eventType === "auth_ok") {
          socketAuthenticated.current = true;
          const conversationId = selectedConversationIdRef.current;
          if (conversationId) {
            void Promise.all([
              loadConversationMessages(conversationId),
              loadConversationAttachments(conversationId),
            ]).catch(() => {
              addToast(
                "Chat refresh failed",
                "Open the conversation again to refresh its messages.",
                "warning",
              );
            });
          }
          // Durable REST state is authoritative for lifecycle events that may
          // have completed while this socket was disconnected.
          void refreshConversations();
        }
        if (eventType === "assistant_consent_required") {
          setSettingsInitialSection("ai");
          setIsSettingsOpen(true);
          addToast(
            settings.interfaceLanguage === "vi" ? "Trợ lý cần quyền truy cập" : "Assistant permission required",
            settings.interfaceLanguage === "vi"
              ? "Bật “Đọc nội dung hội thoại” để trợ lý có thể trả lời yêu cầu này."
              : "Enable “Read conversations” so the assistant can answer this request.",
            "warning",
          );
          return;
        }
        if (eventType === "message_created" || eventType === "message_received") {
          const realtime = payload.message as ApiRealtimeMessage;
          const clientMessageId = payload.client_message_id as string | undefined;
          const realtimeAttachment = realtime.attachment;
          const message = toApiMessageFromRealtime(
            realtime,
            clientMessageId,
            settings.preferredLanguage,
          );
          const mapped = applyDisplayPreference(
            [toMessage(message, usersById, settings.preferredLanguage, translationContextForConversation(message.conversation_id))],
            settings.showOriginalByDefault,
          )[0];
          setAssistantConversation((previous) => previous?.id === mapped.conversationId
            ? { ...previous, lastMessage: mapped.content, lastMessageAt: message.created_at, lastMessageTime: formatRelativeMessageTime(message.created_at, settings.interfaceLanguage) }
            : previous);
          setMessagesMap((previous) => {
            const messages = previous[mapped.conversationId] ?? [];
            return {
              ...previous,
              [mapped.conversationId]: reconcileRealtimeMessage(
                messages,
                mapped,
                clientMessageId,
                currentUser.id,
              ),
            };
          });
          if (realtimeAttachment) {
            const mappedAttachment = toMessageAttachment(realtimeAttachment);
            setAttachmentsMap((previous) => {
              const existing = previous[mapped.conversationId] ?? [];
              return existing.some((attachment) => attachment.id === mappedAttachment.id)
                ? previous
                : { ...previous, [mapped.conversationId]: [mappedAttachment, ...existing] };
            });
          }
          if (mapped.messageType === "voice" && mapped.transcriptionStatus === "pending") {
            scheduleVoiceStatusRefresh(mapped.id, mapped.conversationId);
          }
          void refreshConversations();
        }
        // The assistant found something the user may want on their calendar.
        // It is only a proposal: nothing is scheduled until they approve it in
        // the task inbox.
        if (eventType === "action_proposal_created") {
          const proposal = payload.proposal as ApiActionProposal | undefined;
          if (proposal) {
            // A reconnect can replay the event after the initial REST load.
            // Replace by id so the one review card stays tied to its message.
            setIncomingProposals((current) => [
              proposal,
              ...current.filter((item) => item.id !== proposal.id),
            ].slice(0, 50));
            // The WebSocket event is deliberately small. Reload the durable
            // rows so field-level context (for example, which message supplied
            // the time) appears immediately rather than only after a refresh.
            if (token.current) {
              void listActionProposals(token.current)
                .then((proposals) => setIncomingProposals(proposals))
                .catch(() => undefined);
            }
            addToast("Trợ lý đề xuất một việc", proposal.title, "info");
          }
        }
        // A reminder came due. Kept as a toast rather than anything modal: it
        // arrives while the person is doing something else, and interrupting
        // them to dismiss a box is worse than the reminder is useful.
        if (eventType === "reminder_due") {
          const reminder = payload.reminder as { title?: string; starts_at?: string } | undefined;
          if (reminder) {
            addToast(
              "Sắp đến giờ",
              reminder.title,
              "info",
            );
            if (soundEnabledRef.current) {
              try {
                new Audio("/notification.mp3").play().catch(() => undefined);
              } catch {
                // No sound is not a failure worth surfacing.
              }
            }
          }
        }
        if (eventType === "action_proposal_confirmed" || eventType === "calendar_event_updated") {
          setCalendarRefreshCount((count) => count + 1);
        }
        if (eventType === "voice_transcription_completed") {
          const transcriptionEvent: VoiceTranscriptionCompletedPayload = {
            message_id: String(payload.message_id),
            conversation_id: String(payload.conversation_id),
            original_text: String(payload.original_text),
            transcription_status: "completed",
          };
          cancelVoiceStatusRefresh(transcriptionEvent.message_id);
          setMessagesMap((previous) => ({
            ...previous,
            [transcriptionEvent.conversation_id]: applyVoiceTranscriptionCompleted(
              previous[transcriptionEvent.conversation_id] ?? [],
              transcriptionEvent,
            ),
          }));
          setRetryingTranscriptionIds((previous) => {
            if (!previous.has(transcriptionEvent.message_id)) return previous;
            const next = new Set(previous);
            next.delete(transcriptionEvent.message_id);
            return next;
          });
          void refreshConversations();
        }
        if (eventType === "voice_transcription_failed") {
          const transcriptionEvent: VoiceTranscriptionFailedPayload = {
            message_id: String(payload.message_id),
            conversation_id: String(payload.conversation_id),
            transcription_status: "failed",
            retryable: Boolean(payload.retryable),
          };
          cancelVoiceStatusRefresh(transcriptionEvent.message_id);
          setMessagesMap((previous) => ({
            ...previous,
            [transcriptionEvent.conversation_id]: applyVoiceTranscriptionFailed(
              previous[transcriptionEvent.conversation_id] ?? [],
              transcriptionEvent,
            ),
          }));
          setRetryingTranscriptionIds((previous) => {
            if (!previous.has(transcriptionEvent.message_id)) return previous;
            const next = new Set(previous);
            next.delete(transcriptionEvent.message_id);
            return next;
          });
          void refreshConversations();
        }
        // This is the authoritative result produced by the LangGraph translation
        // agent. The server persists it before publishing, so REST history also
        // restores it after a reconnect.
        if (eventType === "translation_completed" || eventType === "translation.completed") {
          setMessagesMap((previous) => Object.fromEntries(Object.entries(previous).map(([conversationId, messages]) => {
            const conversation = conversations.find((item) => item.id === conversationId);
            return [conversationId, applyTranslationCompleted(messages, {
              message_id: String(payload.message_id),
              translation_id: payload.translation_id,
              source_language: payload.source_language,
              target_language: payload.target_language,
              translated_text: payload.translated_text,
              content: payload.content,
              status: payload.status,
            }, {
              conversation,
              currentUserId: currentUser.id,
              preferredLanguage: settings.preferredLanguage,
              showOriginalByDefault: settings.showOriginalByDefault,
            })];
          })));
          void refreshConversations();
        }
        if (["call_incoming", "call_accepted", "call_rejected", "call_ended", "call_failed"].includes(eventType)) {
          const call: ApiCall = {
            call_id: String(payload.call_id),
            conversation_id: String(payload.conversation_id),
            caller_id: String(payload.caller_id),
            callee_id: String(payload.callee_id),
            call_type: payload.call_type === "video" ? "video" : "voice",
            status: String(payload.status) as ApiCall["status"],
            room_url: null,
            join_token: null,
            created_at: "",
            answered_at: null,
            ended_at: null,
          };
          if (eventType === "call_incoming" && call.callee_id === currentUser.id) {
            setActiveCall(toActiveCall(call, "incoming"));
          }
          if (eventType === "call_accepted" && call.caller_id === currentUser.id && token.current) {
            void joinCall(token.current, call.call_id)
              .then((join) => setActiveCall(toActiveCall(join, "active")))
              .catch((error: unknown) => {
                addToast("Không thể kết nối cuộc gọi", error instanceof Error ? error.message : undefined, "warning");
                setActiveCall(null);
              });
          }
          if (["call_rejected", "call_ended", "call_failed"].includes(eventType)) {
            setActiveCall((current) => current?.id === call.call_id ? null : current);
            addToast(
              eventType === "call_rejected"
                ? "Cuộc gọi bị từ chối"
                : eventType === "call_failed"
                  ? "Cuộc gọi thất bại"
                  : "Cuộc gọi đã kết thúc",
              undefined,
              "info",
            );
          }
        }
        if (eventType === "typing") setConversations((items) => items.map((item) => item.id === payload.conversation_id ? { ...item, isTyping: Boolean(payload.is_typing) } : item));
        if (eventType === "mention") addToast("Bạn được nhắc tới", "Có một tin nhắn mới nhắc đến bạn.", "info");
        if (eventType === "message_updated" || eventType === "message_deleted") {
          const messageId = payload.message_id as string;
          if (eventType === "message_deleted") cancelVoiceStatusRefresh(messageId);
          setMessagesMap((previous) => Object.fromEntries(Object.entries(previous).map(([conversationId, messages]) => [conversationId, messages.map((item) => item.id === messageId ? {
            ...item,
            content: eventType === "message_deleted" ? "This message was deleted" : payload.original_text as string,
            translation: undefined,
            attachments: eventType === "message_deleted" ? undefined : item.attachments,
            deletedAt: eventType === "message_deleted" ? String(payload.deleted_at) : item.deletedAt,
          } : item)])));
          void refreshConversations();
        }
        if (eventType === "message_reactions_updated") {
          const messageId = String(payload.message_id);
          const reactions = Array.isArray(payload.reactions) ? payload.reactions as ApiMessageReaction[] : [];
          setMessagesMap((previous) => Object.fromEntries(Object.entries(previous).map(([conversationId, messages]) => [
            conversationId,
            messages.map((message) => message.id === messageId ? { ...message, reactions: toReactions(reactions) } : message),
          ])));
        }
        if (eventType === "conversation_member_left") {
          const conversationId = String(payload.conversation_id);
          const departedUserId = String(payload.user_id);
          if (departedUserId === currentUser.id) {
            setConversations((items) => items.filter((item) => item.id !== conversationId));
            setSelectedConversationId((selected) => selected === conversationId ? null : selected);
          } else {
            void refreshConversations();
          }
        }
        if (eventType === "error") addToast("Chat error", payload.message as string, "warning");
      };
      ws.onclose = () => {
        socketAuthenticated.current = false;
        if (!disposed) retry = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => { disposed = true; if (retry) window.clearTimeout(retry); socket.current?.close(); };
  }, [addToast, cancelVoiceStatusRefresh, conversations, currentUser.id, loadConversationAttachments, loadConversationMessages, refreshConversations, scheduleVoiceStatusRefresh, settings.interfaceLanguage, settings.preferredLanguage, settings.showOriginalByDefault, translationContextForConversation, usersById]);

  useEffect(() => { soundEnabledRef.current = settings.soundEnabled; }, [settings.soundEnabled]);
  useEffect(() => { document.documentElement.classList.toggle("dark", settings.theme === "dark"); }, [settings.theme]);
  useEffect(() => {
    document.documentElement.lang = settings.interfaceLanguage;
    document.documentElement.dir = settings.interfaceLanguage === "ar" ? "rtl" : "ltr";
  }, [settings.interfaceLanguage]);

  const selectConversation = async (conversation: Conversation) => {
    setIsAssistantChatOpen(false);
    setSelectedConversationId(conversation.id); setMobileView("chat");
    try {
      await Promise.all([
        loadConversationMessages(conversation.id),
        loadConversationAttachments(conversation.id),
        markRead(token.current!, conversation.id),
      ]);
      setConversations((items) => items.map((item) => item.id === conversation.id ? { ...item, unreadCount: 0 } : item));
    } catch (error) { addToast("Could not load messages", error instanceof Error ? error.message : undefined, "warning"); }
  };

  const send = (text: string, replyToMessageId?: string, mentions: MessageMention[] = [], attachmentId?: string, forwardedFromMessageId?: string, destinationConversationId = selectedConversationId, optimisticAttachment?: MessageAttachment) => {
    if (!destinationConversationId || socket.current?.readyState !== WebSocket.OPEN || !socketAuthenticated.current) {
      addToast("Reconnecting", "Your message will send when realtime reconnects.", "warning");
      return;
    }
    const clientMessageId = newClientMessageId();
    const repliedMessage = replyToMessageId ? (messagesMap[destinationConversationId] ?? []).find((message) => message.id === replyToMessageId) : undefined;
    const optimistic: Message = { id: clientMessageId, clientMessageId, senderId: currentUser.id, senderName: currentUser.name, senderAvatar: currentUser.avatar, conversationId: destinationConversationId, content: text, messageType: "text", transcriptionStatus: null, timestamp: "Now", createdAt: new Date().toISOString(), status: "sending", mentions, forwardedFromMessageId, attachments: optimisticAttachment ? [optimisticAttachment] : undefined, replyTo: repliedMessage ? { id: repliedMessage.id, senderName: repliedMessage.senderName || "Message", content: repliedMessage.content } : undefined };
    setMessagesMap((previous) => ({ ...previous, [destinationConversationId]: [...(previous[destinationConversationId] ?? []), optimistic] }));
    socket.current.send(JSON.stringify({ type: "send_message", client_message_id: clientMessageId, conversation_id: destinationConversationId, text, mentions: mentions.map((mention) => ({ type: mention.type, user_id: mention.userId })), reply_to_message_id: replyToMessageId, attachment_id: attachmentId, forwarded_from_message_id: forwardedFromMessageId, client_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }));
  };

  const startConversation = async (user: User) => {
    try {
      const item = await createConversation(token.current!, "direct", [user.id]);
      const conversation = toConversation(item, currentUser.id, settings.interfaceLanguage);
      setUsers((items) => [...items.filter((item) => item.id !== user.id), user]);
      setConversations((items) => [conversation, ...items.filter((value) => value.id !== conversation.id)]);
      await selectConversation(conversation); setActiveTab("chats");
    } catch (error) { addToast("Could not start conversation", error instanceof Error ? error.message : undefined, "warning"); }
  };

  const createGroup = async (name: string, memberIds: string[]) => {
    try {
      const item = await createConversation(token.current!, "group", memberIds, name);
      const conversation = toConversation(item, currentUser.id, settings.interfaceLanguage);
      setConversations((items) => [conversation, ...items]); await selectConversation(conversation); setActiveTab("chats"); addToast("Group created", name, "success");
    } catch (error) { addToast("Could not create group", error instanceof Error ? error.message : undefined, "warning"); }
  };

  const attach = async (file: File, destinationConversationId = selectedConversationId, mentions: MessageMention[] = []) => {
    if (!destinationConversationId || !token.current) return;
    try {
      const attachment = await uploadAttachment(token.current, destinationConversationId, file);
      send(`Shared ${file.name}`, undefined, mentions, attachment.id, undefined, destinationConversationId, toMessageAttachment(attachment));
    }
    catch (error) { addToast("Could not upload attachment", error instanceof Error ? error.message : undefined, "warning"); }
  };

  const sendVoice = async (
    file: File,
    replyToMessageId: string | undefined,
    onStage: (stage: Extract<VoiceRecorderStage, 'uploading' | 'sending'>) => void,
    destinationConversationId = selectedConversationId,
  ) => {
    const conversationId = destinationConversationId;
    const accessToken = token.current;
    const activeSocket = socket.current;
    if (!conversationId || !accessToken || !activeSocket || !socketAuthenticated.current) {
      throw new VoiceRecorderError('voice_send_failed');
    }

    const repliedMessage = replyToMessageId
      ? (messagesMap[conversationId] ?? []).find((message) => message.id === replyToMessageId)
      : undefined;
    const replyTo = repliedMessage ? {
      id: repliedMessage.id,
      senderName: repliedMessage.senderName || "Message",
      content: repliedMessage.content,
    } : undefined;

    await uploadAndSendVoiceMessage({
      token: accessToken,
      conversationId,
      file,
      replyTo,
      socket: activeSocket,
      sender: currentUser,
      onStage,
      onOptimistic: (message) => {
        setMessagesMap((previous) => ({
          ...previous,
          [conversationId]: [...(previous[conversationId] ?? []), message],
        }));
      },
      onOptimisticRejected: (clientMessageId) => {
        setMessagesMap((previous) => ({
          ...previous,
          [conversationId]: (previous[conversationId] ?? []).filter(
            (message) => message.id !== clientMessageId,
          ),
        }));
      },
    });
  };

  // Results go to their own state, never into `users`.
  //
  // `users` is the contact list derived from existing conversations, and
  // `refreshConversations` rewrites it on every socket event — a message, a
  // translation finishing, someone typing. While the picker was open that
  // rewrite landed on top of whatever the search had just returned, so the list
  // flickered between two different sets and a row moved out from under the
  // pointer before the click landed. Keeping the two apart is the fix: an
  // arriving translation can no longer disturb a search in progress.
  /** Decide on a proposal without leaving the conversation.
   *
   *  The row is dropped from the in-chat list on success rather than left
   *  showing a decided state: the card exists to ask a question, and once it is
   *  answered the answer belongs on the calendar, not in the transcript. The
   *  task inbox reloads from the server and shows the outcome there.
   */
  const decideProposal = async (
    proposal: ApiActionProposal,
    run: () => Promise<ApiActionProposal>,
    success: string,
  ) => {
    if (!token.current) return;
    setProposalBusyId(proposal.id);
    try {
      const decided = await run();
      setIncomingProposals((current) =>
        current.map((item) => (item.id === decided.id ? decided : item)),
      );
      addToast(success, proposal.title, "success");
    } catch (error) {
      addToast(
        "Không thực hiện được",
        error instanceof Error ? error.message : undefined,
        "warning",
      );
    } finally {
      setProposalBusyId(null);
    }
  };

  const searchUsers = async (query: string) => {
    if (!token.current) return;
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setUserSearchResults(null);
      return;
    }
    try { setUserSearchResults((await listUsers(token.current, trimmed)).map(toChatUser)); }
    catch (error) { addToast("Could not search contacts", error instanceof Error ? error.message : undefined, "warning"); }
  };

  const downloadSharedFile = async (attachment: MessageAttachment) => {
    if (!token.current) return;
    try {
      await downloadAttachment(token.current, attachment);
    } catch (error) {
      addToast("Could not download file", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const loadSharedFilePreview = useCallback(async (attachment: MessageAttachment) => {
    if (!token.current) throw new Error("Authentication is required");
    const directUrl = await getAttachmentDirectUrl(token.current, attachment);
    if (directUrl) return directUrl;
    return URL.createObjectURL(await fetchAttachmentBlob(token.current, attachment));
  }, []);

  const rateTranslation = async (messageId: string, translationId: string, rating: 1 | 5) => {
    if (!token.current) return;
    try {
      await submitTranslationFeedback(token.current, translationId, rating);
      setMessagesMap((previous) => Object.fromEntries(Object.entries(previous).map(([conversationId, messages]) => [
        conversationId,
        messages.map((message) => message.id === messageId && message.translation
          ? { ...message, translation: { ...message.translation, rating } }
          : message),
      ])));
      addToast("Translation feedback saved", rating === 5 ? "Marked helpful." : "Marked unhelpful.", "success");
    } catch (error) {
      addToast("Could not save feedback", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const editTranslation = async (messageId: string, translationId: string, editedText: string) => {
    if (!token.current) return;
    try {
      const result = await submitTranslationEdit(token.current, translationId, editedText);
      setMessagesMap((previous) => Object.fromEntries(Object.entries(previous).map(([conversationId, messages]) => [
        conversationId,
        messages.map((message) => message.id === messageId && message.translation
          ? { ...message, translation: { ...message.translation, editedText: result.edited_text } }
          : message),
      ])));
      addToast("Translation suggestion saved", "Saved for translation-quality review.", "success");
    } catch (error) {
      addToast("Could not save suggestion", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const openDirectChat = async (userId: string) => {
    const user = usersById.get(userId);
    if (!user || user.id === currentUser.id) return;
    await startConversation(user);
  };

  const searchInConversation = useCallback(async (query: string): Promise<ConversationSearchResult[]> => {
    if (!token.current || !selectedConversationId) return [];
    const result = await searchMessages(token.current, selectedConversationId, query);
    const foundMessages = applyDisplayPreference(
      result.items.map((item) => toMessage(item.message, usersById, settings.preferredLanguage, translationContextForConversation(selectedConversationId))),
      settings.showOriginalByDefault,
    );
    setMessagesMap((previous) => {
      const current = previous[selectedConversationId] ?? [];
      const byId = new Map(current.map((message) => [message.id, message]));
      for (const message of foundMessages) byId.set(message.id, message);
      return { ...previous, [selectedConversationId]: [...byId.values()] };
    });
    return result.items.map((item) => ({
      messageId: item.message.id,
      snippet: item.snippet,
      matchedIn: item.matched_in,
    }));
  }, [selectedConversationId, settings.preferredLanguage, settings.showOriginalByDefault, translationContextForConversation, usersById]);

  const changeConversationPreference = async (
    conversationId: string,
    changes: { is_pinned?: boolean; is_muted?: boolean },
  ) => {
    if (!token.current) return;
    try {
      const updated = await updateConversationPreferences(token.current, conversationId, changes);
      const mapped = toConversation(updated, currentUser.id, settings.interfaceLanguage);
      setConversations((items) => sortConversations(items.map((item) => item.id === conversationId ? mapped : item)));
    } catch (error) {
      addToast("Could not update conversation", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const toggleMute = (conversationId: string) => {
    const conversation = conversations.find((item) => item.id === conversationId);
    if (conversation) void changeConversationPreference(conversationId, { is_muted: !conversation.isMuted });
  };

  const togglePin = (conversationId: string) => {
    const conversation = conversations.find((item) => item.id === conversationId);
    if (conversation) void changeConversationPreference(conversationId, { is_pinned: !conversation.isPinned });
  };

  const leaveSelectedGroup = async (conversationId: string) => {
    if (!token.current) return;
    try {
      await leaveConversation(token.current, conversationId);
      setConversations((items) => items.filter((item) => item.id !== conversationId));
      setMessagesMap((items) => {
        const { [conversationId]: _removed, ...rest } = items;
        return rest;
      });
      if (selectedConversationId === conversationId) setSelectedConversationId(null);
      addToast("Left group", undefined, "success");
    } catch (error) {
      addToast("Could not leave group", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const blockConversationContact = async (conversationId: string) => {
    if (!token.current) return;
    const conversation = conversations.find((item) => item.id === conversationId);
    if (!conversation?.recipient) return;
    try {
      await blockContact(token.current, conversation.recipient.id);
      addToast("Contact blocked", conversation.recipient.name, "success");
    } catch (error) {
      addToast("Could not block contact", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const toggleSavedMessage = async (messageId: string) => {
    if (!token.current || !selectedConversationId) return;
    const message = (messagesMap[selectedConversationId] ?? []).find((item) => item.id === messageId);
    if (!message) return;
    try {
      const result = message.isSaved
        ? await unsaveMessage(token.current, selectedConversationId, messageId)
        : await saveMessage(token.current, selectedConversationId, messageId);
      setMessagesMap((previous) => ({
        ...previous,
        [selectedConversationId]: (previous[selectedConversationId] ?? []).map((item) => item.id === messageId
          ? { ...item, isSaved: result.is_saved }
          : item),
      }));
    } catch (error) {
      addToast("Could not save message", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const toggleReaction = async (messageId: string, emoji: string) => {
    if (!token.current || !selectedConversationId) return;
    const message = (messagesMap[selectedConversationId] ?? []).find((item) => item.id === messageId);
    const reacted = Boolean(message?.reactions?.some((reaction) => reaction.emoji === emoji && reaction.users.includes(currentUser.id)));
    try {
      const result = reacted
        ? await removeReaction(token.current, selectedConversationId, messageId, emoji)
        : await addReaction(token.current, selectedConversationId, messageId, emoji);
      setMessagesMap((previous) => ({
        ...previous,
        [selectedConversationId]: (previous[selectedConversationId] ?? []).map((item) => item.id === messageId
          ? { ...item, reactions: toReactions(result.reactions) }
          : item),
      }));
    } catch (error) {
      addToast("Could not update reaction", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const requestTranslationRetry = async (messageId: string) => {
    if (!token.current || !selectedConversationId) return;
    try {
      const message = (messagesMap[selectedConversationId] ?? []).find((item) => item.id === messageId);
      const conversation = conversations.find((item) => item.id === selectedConversationId);
      const reviewRecipient = Boolean(
        message?.senderId === currentUser.id && conversation?.type === 'direct',
      );
      await retryTranslation(token.current, selectedConversationId, messageId, reviewRecipient);
      setMessagesMap((previous) => ({
        ...previous,
        [selectedConversationId]: (previous[selectedConversationId] ?? []).map((message) => message.id === messageId && message.translation
          ? { ...message, translation: { ...message.translation, status: "pending" } }
          : message),
      }));
      addToast("Translation requested", "A fresh translation is being generated.", "translation");
    } catch (error) {
      addToast("Could not retry translation", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const requestVoiceTranscriptionRetry = async (messageId: string) => {
    if (!token.current || retryingTranscriptionIdsRef.current.has(messageId)) return;
    retryingTranscriptionIdsRef.current.add(messageId);
    setRetryingTranscriptionIds((previous) => new Set(previous).add(messageId));
    try {
      const result = await retryVoiceTranscription(token.current, messageId);
      setMessagesMap((previous) => ({
        ...previous,
        [result.conversation_id]: applyVoiceTranscriptionRetryAccepted(
          previous[result.conversation_id] ?? [],
          result.message_id,
          result.conversation_id,
        ),
      }));
      // The detached task can finish before this HTTP response reaches the
      // browser. Rehydrate from durable history so an already-published
      // completion/failure event cannot leave this bubble stuck at pending.
      try {
        await loadConversationMessages(result.conversation_id);
      } catch {
        // The retry itself was accepted. The bounded follow-up refreshes below
        // can still recover its persisted terminal state after a transient
        // history request failure.
      }
      scheduleVoiceStatusRefresh(result.message_id, result.conversation_id);
      void refreshConversations();
    } catch {
      addToast(
        interactionText(settings.interfaceLanguage, "Could not retry transcription"),
        undefined,
        "warning",
      );
    } finally {
      retryingTranscriptionIdsRef.current.delete(messageId);
      setRetryingTranscriptionIds((previous) => {
        if (!previous.has(messageId)) return previous;
        const next = new Set(previous);
        next.delete(messageId);
        return next;
      });
    }
  };

  const saveProfile = async (value: Partial<User>) => {
    if (!token.current) return;
    try {
      const changes: { display_name?: string; bio?: string; avatar_url?: string } = {
        display_name: value.name,
        bio: value.bio,
      };
      // Generated fallback avatars are display-only URLs. Only persist a
      // client-generated data URL when the user actually selected a new photo.
      if (value.avatar?.startsWith("data:image/")) changes.avatar_url = value.avatar;
      const profile = await updateProfile(token.current, changes);
      setCurrentUser((previous) => ({ ...previous, ...toChatUser(profile) }));
      addToast("Profile saved", undefined, "success");
    } catch (error) {
      addToast("Could not save profile", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const closeGroup = async (conversationId: string, removeForEveryone: boolean) => {
    if (!token.current) return;
    try {
      if (removeForEveryone) await deleteGroup(token.current, conversationId);
      else await leaveGroup(token.current, conversationId);
      setConversations((items) => items.filter((item) => item.id !== conversationId));
      setSelectedConversationId(null);
      addToast(removeForEveryone ? "Group deleted" : "Left group", undefined, "success");
    } catch (error) {
      addToast("Group action failed", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const manageGroup = async (action: () => Promise<void>, success: string) => {
    try {
      await action();
      await refreshConversations();
      addToast(success, undefined, "success");
    } catch (error) {
      addToast("Could not update group", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const deleteOwnMessage = async (messageId: string) => {
    if (!token.current || !activeConversationId) return;
    try {
      await deleteMessage(token.current, activeConversationId, messageId);
      await Promise.all([
        loadConversationMessages(activeConversationId),
        loadConversationAttachments(activeConversationId),
        refreshConversations(),
      ]);
      addToast("Message deleted", "The message has been removed for everyone.", "success");
    } catch (error) {
      addToast("Could not delete message", error instanceof Error ? error.message : undefined, "warning");
    }
  };

  const handleUpdateAgentConsents = useCallback(
    (changes: Partial<Record<AgentConsentScope, boolean>>) => {
      if (!token.current) {
        addToast("Could not save permissions", "Please sign in again.", "warning");
        return;
      }
      // No optimistic update. A switch that flips and then silently flips back
      // would leave someone believing they had withdrawn a permission they
      // still hold, which is the one mistake this screen must not make.
      void updateAgentConsents(token.current, changes)
        .then((saved) => {
          setAgentConsents(toConsentMap(saved));
          setAgentConsentsAnswered(consentsAllAnswered(saved));
        })
        .catch((error: unknown) =>
          addToast(
            "Could not save permissions",
            error instanceof Error ? error.message : undefined,
            "warning",
          ),
        );
    },
    [addToast],
  );

  const handleUpdateSettings = useCallback((value: Partial<AppSettings>) => {
    const serverChanges: Partial<ApiUserSettings> = {};
    if (value.autoTranslate !== undefined) serverChanges.auto_translate = value.autoTranslate;
    if (value.showOriginalByDefault !== undefined) serverChanges.show_original_by_default = value.showOriginalByDefault;
    if (value.translationTone !== undefined) serverChanges.translation_tone = value.translationTone;
    if (value.soundEnabled !== undefined) serverChanges.sound_enabled = value.soundEnabled;
    if (value.readReceipts !== undefined) serverChanges.read_receipts = value.readReceipts;
    if (value.aiSmartAssistance !== undefined) serverChanges.ai_smart_assistance = value.aiSmartAssistance;
    if (Object.keys(serverChanges).length > 0) {
      if (!token.current) {
        addToast("Could not save settings", "Please sign in again.", "warning");
      } else {
        void updateUserSettings(token.current, serverChanges)
          .then((saved) => {
            setSettings((previous) => ({ ...previous, ...persistedSettings(saved) }));
            if (value.showOriginalByDefault !== undefined) {
              setMessagesMap((previous) => Object.fromEntries(Object.entries(previous).map(([conversationId, messages]) => [
                conversationId,
                applyDisplayPreference(messages, saved.show_original_by_default),
              ])));
            }
          })
          .catch((error: unknown) => addToast("Could not save settings", error instanceof Error ? error.message : undefined, "warning"));
      }
    }
    const requestedInterfaceLanguage = value.interfaceLanguage;
    if (requestedInterfaceLanguage && requestedInterfaceLanguage !== settings.interfaceLanguage && token.current) {
      void updateInterfaceLanguage(token.current, requestedInterfaceLanguage)
        .then(() => setSettings((previous) => ({ ...previous, interfaceLanguage: requestedInterfaceLanguage })))
        .catch((error: unknown) => addToast("Could not save interface language", error instanceof Error ? error.message : undefined, "warning"));
    }
    const requestedLanguage = value.preferredLanguage;
    if (!requestedLanguage || requestedLanguage === settings.preferredLanguage) {
      if (value.theme !== undefined || value.offlineModeSimulation !== undefined) {
        setSettings((previous) => ({ ...previous, theme: value.theme ?? previous.theme, offlineModeSimulation: value.offlineModeSimulation ?? previous.offlineModeSimulation }));
      }
      return;
    }
    if (!token.current) {
      addToast("Could not save language", "Please sign in again.", "warning");
      return;
    }

    void updatePreferredLanguage(token.current, requestedLanguage)
      .then(async (profile) => {
        const preferredLanguage = toLanguageCode(profile.preferred_language);
        setCurrentUser((previous) => ({ ...previous, nativeLanguage: preferredLanguage }));
        setSettings((previous) => ({ ...previous, preferredLanguage }));

        if (selectedConversationId && token.current) {
          const history = await getMessages(token.current, selectedConversationId);
          setMessagesMap((previous) => ({
            ...previous,
            [selectedConversationId]: applyDisplayPreference(
              toMessages(history, usersById, preferredLanguage, translationContextForConversation(selectedConversationId)),
              settings.showOriginalByDefault,
            ),
          }));
        }
        addToast("Language updated", "New messages will be translated in your selected language.", "success");
      })
      .catch((error: unknown) => {
        addToast("Could not save language", error instanceof Error ? error.message : undefined, "warning");
      });
  }, [addToast, selectedConversationId, settings.interfaceLanguage, settings.preferredLanguage, settings.showOriginalByDefault, translationContextForConversation, usersById]);

  const unreadChatsCount = conversations.reduce((total, item) => total + item.unreadCount, 0);
  const activeCallConversation = activeCall
    ? conversations.find((conversation) => conversation.id === activeCall.conversationId) ?? null
    : null;
  if (!currentUser.id) {
    return (
      <main className="grid h-screen place-items-center bg-[#F7F8FC] px-6 text-center text-sm text-[#596174]">
        Đang kiểm tra phiên đăng nhập…
      </main>
    );
  }

  return <div id="linguaflow-app-shell" className="flex w-screen h-screen overflow-hidden bg-[#F7F8FC] dark:bg-[#14161C] select-none">
    {settings.offlineModeSimulation && <div className="absolute top-0 inset-x-0 z-50 flex items-center justify-center gap-2 py-1 px-4 bg-amber-500 text-white text-xs font-semibold"><WifiOff className="w-3.5 h-3.5" />You&apos;re offline. Messages will send automatically when you reconnect.</div>}
    <div className={mobileView === "chat" ? "hidden md:flex" : "flex"}><MiniSidebar activeTab={activeTab} onTabChange={(tab) => { if (tab === "settings") { setSettingsInitialSection("language"); setIsSettingsOpen(true); return; } setActiveTab(tab); if (tab !== "chats") setIsAssistantChatOpen(false); }} currentUser={currentUser} settings={settings} onOpenSettings={() => { setSettingsInitialSection("language"); setIsSettingsOpen(true); }} onToggleTheme={() => setSettings((value) => ({ ...value, theme: value.theme === "dark" ? "light" : "dark" }))} onLogout={handleLogout} isLoggingOut={isLoggingOut} unreadChatsCount={unreadChatsCount} pendingTaskCount={pendingTaskCount} /></div>
    {(activeTab === "chats" || activeTab === "contacts" || activeTab === "groups") && <div className={`h-screen flex-shrink-0 ${mobileView === "chat" ? "hidden md:flex" : "flex w-full md:w-[340px]"}`}>
      {activeTab === "chats" && <ConversationPanel conversations={conversations} selectedConversationId={selectedConversationId} onSelectConversation={selectConversation} onOpenNewChat={() => setIsNewChatOpen(true)} onMarkAllAsRead={() => conversations.forEach((item) => void markRead(token.current!, item.id))} assistantSelected={isAssistantChatOpen} onOpenAssistant={() => {
        if (!token.current) return;
        void getAssistantConversation(token.current).then(async (item) => {
          const assistant = toAssistantConversation(item, currentUser.id, settings.interfaceLanguage);
          setAssistantConversation(assistant);
          setIsAssistantChatOpen(true);
          setSelectedConversationId(null);
          setMobileView("chat");
          const [history, attachments] = await Promise.all([
            getMessages(token.current!, assistant.id),
            listAttachments(token.current!, assistant.id).catch(() => []),
          ]);
          const messages = toMessages(history, usersById, settings.preferredLanguage);
          const latest = messages.at(-1);
          const latestSource = history.at(-1);
          if (latest && latestSource) {
            setAssistantConversation((previous) => previous?.id === assistant.id
              ? { ...previous, lastMessage: latest.content, lastMessageAt: latestSource.created_at, lastMessageTime: formatRelativeMessageTime(latestSource.created_at, settings.interfaceLanguage) }
              : previous);
          }
          setMessagesMap((previous) => ({ ...previous, [assistant.id]: messages }));
          setAttachmentsMap((previous) => ({ ...previous, [assistant.id]: attachments.map(toMessageAttachment) }));
        }).catch((error: unknown) => addToast("Không thể mở Trợ lý", error instanceof Error ? error.message : undefined, "warning"));
      }} assistantLastMessage={assistantConversation?.lastMessage || tx(settings.interfaceLanguage, "Hello! How can I help?")} assistantLastMessageTime={assistantConversation?.lastMessageTime || tx(settings.interfaceLanguage, "Now")} language={settings.interfaceLanguage} />}
      {activeTab === "contacts" && <ContactsPanel users={users} onSearchUsers={searchUsers} onStartChatWithUser={startConversation} onOpenNewChat={() => setIsNewChatOpen(true)} language={settings.interfaceLanguage} />}
      {activeTab === "groups" && <GroupsPanel conversations={conversations} selectedConversationId={selectedConversationId} onSelectConversation={selectConversation} onCreateGroupClick={() => setIsCreateGroupOpen(true)} language={settings.interfaceLanguage} />}
    </div>}
    <div className={`flex-1 flex min-w-0 h-screen overflow-hidden ${activeTab === "calendar" ? "w-full" : mobileView === "list" ? "hidden md:flex" : "flex w-full"}`}>
      {/* Guarded on the token rather than defaulting it to "": an empty bearer
          would turn "not signed in yet" into a 401 the user has to interpret,
          and this shell is already on its way to the login screen without one. */}
      {activeTab === "calendar" && accessToken ? (
        <PersonalCalendar
          token={accessToken}
          language={settings.interfaceLanguage}
          onNotify={addToast}
          refreshToken={calendarRefreshCount}
        />
      ) : activeTab === "tasks" && accessToken ? (
        <TaskInboxPanel
          token={accessToken}
          language={settings.interfaceLanguage}
          incoming={incomingProposals}
          onProposalChanged={(proposal, removed) =>
            setIncomingProposals((current) => removed
              ? current.filter((item) => item.id !== proposal.id)
              : current.map((item) => (item.id === proposal.id ? proposal : item)))}
          onCountChange={setPendingTaskCount}
          onNotify={addToast}
        />
      ) :
      <ChatView
        conversation={activeConversation}
        messages={currentMessages}
        currentUser={currentUser}
        onBack={() => setMobileView("list")}
        onSendMessage={(text, replyToMessageId, mentions) => isAssistantChatOpen && activeConversationId
          ? send(text, replyToMessageId, [{ type: "assistant" }], undefined, undefined, activeConversationId)
          : send(text, replyToMessageId, mentions)}
        onSendAttachment={(file) => isAssistantChatOpen && activeConversationId
          ? void attach(file, activeConversationId, [{ type: "assistant" }])
          : void attach(file)}
        onSendVoice={isAssistantChatOpen && activeConversationId
          ? (file, replyToMessageId, onStage) => sendVoice(file, replyToMessageId, onStage, activeConversationId)
          : sendVoice}
        onTyping={(isTyping) => activeConversationId && socket.current?.readyState === WebSocket.OPEN && socket.current.send(JSON.stringify({ type: "typing", conversation_id: activeConversationId, is_typing: isTyping }))}
        onReact={(messageId, emoji) => { if (!isAssistantChatOpen) void toggleReaction(messageId, emoji); }}
        onCopy={(text) => void navigator.clipboard.writeText(text)}
        onSpeak={async (messageId, language, source) => {
          if (!token.current) return;
          try {
            const response = await fetchTTSText(
              token.current,
              messageId,
              source === "translation" ? language : undefined,
            );
            tts.speak(
              response.text,
              response.language,
              messageId,
              () => fetchTTSAudio(
                token.current!,
                messageId,
                source === "translation" ? language : undefined,
              ),
            );
          } catch (error) {
            addToast(shellChromeText(settings.interfaceLanguage, "ttsUnavailable"), error instanceof Error ? error.message : undefined, "warning");
          }
        }}
        onStopSpeak={tts.stop}
        speakingMessageId={tts.currentMessageId}
        onToggleOriginal={(id) => activeConversationId && setMessagesMap((items) => ({ ...items, [activeConversationId]: (items[activeConversationId] ?? []).map((message) => message.id === id && message.translation ? { ...message, translation: { ...message.translation, showOriginal: !message.translation.showOriginal } } : message) }))}
        onRetryTranslation={(messageId) => { if (!isAssistantChatOpen) void requestTranslationRetry(messageId); }}
        onRetryTranscription={(messageId) => { if (!isAssistantChatOpen) void requestVoiceTranscriptionRetry(messageId); }}
        retryingTranscriptionIds={retryingTranscriptionIds}
        onRateTranslation={rateTranslation}
        onEditTranslation={editTranslation}
        onForward={setForwardingMessage}
        onSaveMessage={(messageId) => void toggleSavedMessage(messageId)}
        onDeleteMessage={(id) => void deleteOwnMessage(id)}
        onToggleMute={toggleMute}
        onTogglePin={togglePin}
        onBlockContact={(conversationId) => void blockConversationContact(conversationId)}
        onSearchMessages={searchInConversation}
        onOpenNewChat={() => setIsNewChatOpen(true)}
        calendarToken={accessToken ?? undefined}
        onCreateAppointment={(task) => {
          if (!token.current) return;
          // The calendar editor can offer several presentation choices, but the
          // persisted reminder is the in-app notification. Preserve its chosen
          // lead time instead of silently falling back to the API's 15-minute
          // default.
          const reminder = task.reminders?.find((item) => item.method === "popup");
          void createCalendarEvent(token.current, {
            title: task.title,
            starts_at: task.dueAt,
            ends_at: task.endAt ?? null,
            details: task.note ?? null,
            location: task.location ?? null,
            all_day: task.isAllDay ?? false,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            reminder_minutes_before: reminder?.minutes ?? null,
          }).then(() => {
            addToast("Đã tạo nhắc hẹn", "Sự kiện đã được thêm vào lịch của bạn.", "success");
            setCalendarRefreshCount((count) => count + 1);
          }).catch((error: unknown) => addToast("Không tạo được nhắc hẹn", error instanceof Error ? error.message : undefined, "warning"));
        }}
        onScanAppointments={async (conversationId, amount, unit) => {
          if (!token.current) throw new Error('Vui lòng đăng nhập lại để tìm lịch hẹn.');
          try {
            const created = await scanConversationForActionProposals(token.current, conversationId, amount, unit);
            if (created.length) {
              setIncomingProposals((current) => {
                const byId = new Map(current.map((proposal) => [proposal.id, proposal]));
                for (const proposal of created) byId.set(proposal.id, proposal);
                return [...byId.values()];
              });
            } else {
              // The backend stores an assistant-authored empty result. Reload
              // as a fallback when its realtime event raced this REST response
              // or the socket was reconnecting.
              await loadConversationMessages(conversationId);
            }
            addToast(
              created.length ? "Đã tạo đề xuất lịch" : "Không tìm thấy lịch hẹn",
              created.length ? `${created.length} đề xuất đang chờ duyệt.` : `Không có lịch hẹn phù hợp trong ${amount} ${unit === 'hours' ? 'giờ' : unit === 'weeks' ? 'tuần' : 'ngày'} đã chọn.`,
              "success",
            );
            return created;
          } catch (error) {
            if (
              error instanceof ChatApiError
              && error.code === "CONSENT_REQUIRED"
              && error.scope === "read_conversations"
            ) {
              setSettingsInitialSection("ai");
              setIsSettingsOpen(true);
              const message = settings.interfaceLanguage === "vi"
                ? "Bật quyền “Đọc nội dung hội thoại” trong Công cụ AI rồi thử lại."
                : "Enable “Read conversations” in AI Tools and try again.";
              addToast(
                settings.interfaceLanguage === "vi" ? "Cần cấp quyền cho trợ lý" : "Assistant permission required",
                message,
                "warning",
              );
              throw new Error(message);
            }
            addToast("Không thể quét tin nhắn", error instanceof Error ? error.message : undefined, "warning");
            throw error;
          }
        }}
        pendingProposals={currentProposals}
        proposalBusyId={proposalBusyId}
        onApproveProposal={(proposal, corrections) => void decideProposal(
          proposal,
          () => confirmActionProposal(token.current!, proposal.id, corrections),
          "Đã duyệt và thêm vào lịch",
        )}
        onRejectProposal={(proposal) => void decideProposal(
          proposal,
          () => rejectActionProposal(token.current!, proposal.id),
          "Đã từ chối",
        )}
        onStartCall={(type) => void initiateCall(type)}
        language={settings.interfaceLanguage}
        attachments={currentAttachments}
        onDownloadAttachment={downloadSharedFile}
        onLoadAttachmentPreview={loadSharedFilePreview}
        onLeaveGroup={(id) => void closeGroup(id, false)}
        onDeleteGroup={(id) => void closeGroup(id, true)}
        availableUsers={users}
        onAddMembers={(id, userIds) => token.current && void manageGroup(() => addGroupMembers(token.current!, id, userIds), "Members added")}
        onRemoveMember={(id, userId) => token.current && void manageGroup(() => removeGroupMember(token.current!, id, userId), "Member removed")}
        onChangeMemberRole={(id, userId, role) => token.current && void manageGroup(() => updateGroupRole(token.current!, id, userId, role), role === "admin" ? "Admin assigned" : "Admin removed")}
        onSearchUsers={(query) => void searchUsers(query)}
        onTransferOwnership={(id, userId) => token.current && void manageGroup(() => transferGroupOwnership(token.current!, id, userId), "Ownership transferred")}
        onUpdateGroup={(id, title, description) => token.current && void manageGroup(() => updateGroupDetails(token.current!, id, title, description), "Group information updated")}
        onStartDirectChat={(userId) => void openDirectChat(userId)}
        assistantMode={isAssistantChatOpen}
      />}
    </div>
    <NewConversationModal isOpen={isNewChatOpen} onClose={() => { setIsNewChatOpen(false); setUserSearchResults(null); }} onSelectUser={startConversation} onCreateGroupClick={() => setIsCreateGroupOpen(true)} users={users} searchResults={userSearchResults} onSearchUsers={searchUsers} language={settings.interfaceLanguage} />
    <CreateGroupModal isOpen={isCreateGroupOpen} onClose={() => setIsCreateGroupOpen(false)} onCreateGroup={createGroup} users={users} onSearchUsers={searchUsers} language={settings.interfaceLanguage} />
    <SettingsModal key={settingsInitialSection} isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} settings={settings} onUpdateSettings={handleUpdateSettings} currentUser={currentUser} onUpdateUser={(value) => void saveProfile(value)} agentConsents={agentConsents} agentConsentsAnswered={agentConsentsAnswered} onUpdateAgentConsents={handleUpdateAgentConsents} initialSection={settingsInitialSection} />
    <CallModal
      key={activeCall?.id ?? "no-active-call"}
      call={activeCall}
      conversation={activeCallConversation}
      onAccept={() => void acceptIncomingCall()}
      onReject={() => void rejectIncomingCall()}
      onEnd={() => void endCurrentCall()}
      onMediaError={handleCallMediaError}
      language={settings.interfaceLanguage}
    />
    <ToastContainer toasts={toasts} onDismiss={(id) => setToasts((items) => items.filter((item) => item.id !== id))} />
    <ForwardMessageModal message={forwardingMessage} conversations={conversations} onClose={() => setForwardingMessage(null)} onStartNewChat={() => { setForwardingMessage(null); setIsNewChatOpen(true); }} onSelect={(target) => { if (!forwardingMessage) return; send(forwardingMessage.content, undefined, [], undefined, forwardingMessage.id, target.id); setForwardingMessage(null); addToast("Message forwarded", `Sent to ${target.name}.`, "success"); }} language={settings.interfaceLanguage} />
  </div>;
};
