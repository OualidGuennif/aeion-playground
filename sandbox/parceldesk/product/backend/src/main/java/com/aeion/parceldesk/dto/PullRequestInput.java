package com.aeion.parceldesk.dto;

import com.fasterxml.jackson.databind.JsonNode;

public record PullRequestInput(
        String missionId,
        String ticketId,
        String title,
        String risk,
        String diff,
        String commitHash,
        String packetDigest,
        String branch,
        String base,
        JsonNode checks,
        JsonNode review) {
}
