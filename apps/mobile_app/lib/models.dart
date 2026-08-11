/// Vioris mobile — domain models (Phase 5, Prompt 5.2).
library;

/// A device JWT grant from the paired gateway.
class DeviceSession {
  const DeviceSession({required this.deviceId, required this.jwt, this.expiresAt});

  final String deviceId;
  final String jwt;
  final double? expiresAt;

  factory DeviceSession.fromJson(Map<String, dynamic> json) => DeviceSession(
        deviceId: json['device_id'] as String,
        jwt: json['jwt'] as String,
        expiresAt: (json['expires_at'] as num?)?.toDouble(),
      );
}

/// Diff card shown for an execute/critical step awaiting a decision.
class Approval {
  const Approval({
    required this.approvalId,
    required this.taskId,
    required this.stepId,
    required this.tool,
    required this.status,
    this.diffCard = const {},
  });

  final String approvalId;
  final String taskId;
  final String stepId;
  final String tool;
  final String status;
  final Map<String, dynamic> diffCard;

  factory Approval.fromJson(Map<String, dynamic> json) => Approval(
        approvalId: json['approval_id'] as String,
        taskId: json['task_id'] as String,
        stepId: json['step_id'] as String,
        tool: (json['tool'] as String?) ?? '',
        status: (json['status'] as String?) ?? 'pending',
        diffCard: (json['diff_card'] as Map<String, dynamic>?) ?? const {},
      );

  /// Short human line like "Send a message" derived from the tool name.
  String get summary => tool.split('.').last.replaceAll('_', ' ');
}

/// A runnable step inside a task.
class TaskStep {
  const TaskStep({
    required this.stepId,
    required this.tool,
    required this.riskLevel,
    required this.status,
    this.error,
  });

  final String stepId;
  final String tool;
  final String riskLevel;
  final String status;
  final String? error;

  factory TaskStep.fromJson(Map<String, dynamic> json) => TaskStep(
        stepId: json['step_id'] as String,
        tool: (json['tool'] as String?) ?? '',
        riskLevel: (json['risk_level'] as String?) ?? 'observe',
        status: (json['status'] as String?) ?? 'created',
        error: json['error'] as String?,
      );
}

/// A task the task-runner is executing.
class Task {
  const Task({
    required this.taskId,
    required this.request,
    required this.status,
    required this.steps,
  });

  final String taskId;
  final String request;
  final String status;
  final List<TaskStep> steps;

  factory Task.fromJson(Map<String, dynamic> json) => Task(
        taskId: json['task_id'] as String,
        request: (json['request'] as String?) ?? '',
        status: (json['status'] as String?) ?? 'created',
        steps: ((json['steps'] as List<dynamic>?) ?? [])
            .map((s) => TaskStep.fromJson(s as Map<String, dynamic>))
            .toList(),
      );
}

/// A live remote-control session granted to this device (Phase 5.3).
class RemoteSession {
  const RemoteSession({
    required this.sessionId,
    required this.deviceId,
    this.expired = false,
    this.ttlSeconds = 0,
  });

  final String sessionId;
  final String deviceId;
  final bool expired;
  final int ttlSeconds;

  factory RemoteSession.fromJson(Map<String, dynamic> json) => RemoteSession(
        sessionId: json['session_id'] as String,
        deviceId: json['device_id'] as String,
        expired: (json['expired'] as bool?) ?? false,
        ttlSeconds: (json['ttl_seconds'] as int?) ?? 0,
      );
}

/// One audit/activity entry from the gateway timeline.
class ActivityEvent {
  const ActivityEvent({
    required this.action,
    required this.actor,
    required this.createdAt,
    this.taskId,
    this.detail = const {},
  });

  final String action;
  final String actor;
  final String createdAt;
  final String? taskId;
  final Map<String, dynamic> detail;

  factory ActivityEvent.fromJson(Map<String, dynamic> json) => ActivityEvent(
        action: (json['action'] as String?) ?? '',
        actor: (json['actor'] as String?) ?? 'system',
        createdAt: (json['created_at'] as String?) ?? '',
        taskId: json['task_id'] as String?,
        detail: (json['detail'] as Map<String, dynamic>?) ?? const {},
      );
}