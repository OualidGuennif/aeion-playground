package com.aeion.parceldesk.model;

import io.quarkus.hibernate.orm.panache.PanacheEntityBase;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "pull_requests")
public class PullRequest extends PanacheEntityBase {

    @Id
    public String id;

    @Column(unique = true, nullable = false)
    public String missionId;

    public String ticketId;
    public String title;
    public String risk;

    @Enumerated(EnumType.STRING)
    public PullRequestStatus status;

    @Lob
    public String diff;

    @Lob
    public String checksJson;

    @Lob
    public String reviewJson;

    public String commitHash;
    public String packetDigest;
    public String branch;
    public String base;
    public String author;
    public String reviewer;
    public Instant createdAt;
    public Instant mergedAt;
    public String mergedBy;
}
