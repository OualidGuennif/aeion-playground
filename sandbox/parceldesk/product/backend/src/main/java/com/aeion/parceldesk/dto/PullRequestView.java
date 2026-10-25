package com.aeion.parceldesk.dto;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.Instant;

public record PullRequestView(
        String id,
        String ticketId,
        String missionId,
        String title,
        String risk,
        String status,
        String diff,
        JsonNode checks,
        JsonNode review,
        String commitHash,
        String packetDigest,
        String branch,
        String base,
        String author,
        String reviewer,
        Instant createdAt,
        Instant mergedAt,
        String mergedBy) {
}
