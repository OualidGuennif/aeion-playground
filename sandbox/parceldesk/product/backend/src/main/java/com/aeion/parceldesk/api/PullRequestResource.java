package com.aeion.parceldesk.api;

import com.aeion.parceldesk.dto.MergeInput;
import com.aeion.parceldesk.dto.PullRequestInput;
import com.aeion.parceldesk.dto.PullRequestView;
import com.aeion.parceldesk.dto.ReviewInput;
import com.aeion.parceldesk.model.PullRequest;
import com.aeion.parceldesk.model.PullRequestStatus;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.inject.Inject;
import jakarta.transaction.Transactional;
import jakarta.ws.rs.BadRequestException;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.NotFoundException;
import jakarta.ws.rs.WebApplicationException;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.core.Response;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

@Path("/api/pulls")
public class PullRequestResource {

    private static final Pattern MISSION = Pattern.compile("[A-Za-z0-9._:-]{1,64}");
    private static final Pattern RISK = Pattern.compile("low|medium|high");
    private static final Set<String> REVIEW_VERDICTS = Set.of("OK", "CHANGES_REQUESTED");

    @Inject
    ObjectMapper mapper;

    private static WebApplicationException conflict(String message) {
        return new WebApplicationException(message, Response.Status.CONFLICT);
    }

    @GET
    public List<PullRequestView> pulls() {
        return PullRequest.<PullRequest>list("order by createdAt desc").stream().map(this::view).toList();
    }

    @GET
    @Path("/{id}")
    public PullRequestView pull(@PathParam("id") String id) {
        return view(find(id));
    }

    @POST
    @Transactional
    public PullRequestView create(PullRequestInput input) {
        if (input == null || input.missionId() == null || !MISSION.matcher(input.missionId()).matches()) {
            throw new BadRequestException("A valid missionId is required.");
        }
        if (input.title() == null || input.title().length() < 4 || input.title().length() > 200) {
            throw new BadRequestException("A pull request title requires 4-200 characters.");
        }
        if (input.risk() == null || !RISK.matcher(input.risk()).matches()) {
            throw new BadRequestException("Risk must be low, medium or high.");
        }
        String diff = input.diff() == null ? "" : input.diff();
        if (diff.length() > 200_000) {
            throw new BadRequestException("The diff exceeds the review bound.");
        }
        JsonNode checks = input.checks() == null ? mapper.createObjectNode() : input.checks();
        JsonNode review = input.review() == null ? mapper.createObjectNode() : input.review();
        if (!checks.isObject() || !review.isObject()) {
            throw new BadRequestException("Checks and review must be JSON objects.");
        }

        PullRequest pr = PullRequest.find("missionId", input.missionId()).firstResult();
        boolean created = pr == null;
        if (created) {
            pr = new PullRequest();
            pr.id = "PR-" + String.format("%03d", PullRequest.count() + 1);
            pr.missionId = input.missionId();
            pr.createdAt = Instant.now();
            pr.author = "forge";
        }
        pr.ticketId = input.ticketId();
        pr.title = input.title();
        pr.risk = input.risk();
        pr.diff = diff;
        pr.commitHash = input.commitHash();
        pr.packetDigest = input.packetDigest();
        pr.branch = input.branch() == null ? "factory/" + input.missionId() : input.branch();
        pr.base = input.base();
        pr.reviewer = review.path("model").asText("sentinel");
        pr.checksJson = write(checks);
        pr.reviewJson = write(review);
        if (!created && pr.status == PullRequestStatus.MERGED) {
            // A merged pull request keeps its terminal state on a re-sync.
        } else if (!created && pr.status == PullRequestStatus.CLOSED) {
            // A closed pull request is not reopened by a sync.
        } else {
            pr.status = checksOk(checks) && reviewOk(review)
                    ? PullRequestStatus.OPEN : PullRequestStatus.BLOCKED;
        }
        if (created) {
            pr.persist();
        }
        return view(pr);
    }

    @POST
    @Path("/{id}/review")
    @Transactional
    public PullRequestView review(@PathParam("id") String id, ReviewInput input) {
        PullRequest pr = find(id);
        if (pr.status == PullRequestStatus.MERGED || pr.status == PullRequestStatus.CLOSED) {
            throw conflict("This pull request is already closed.");
        }
        if (input == null) {
            throw new BadRequestException("A review body is required.");
        }
        String verdict = input.verdict() == null ? "" : input.verdict().toUpperCase();
        if (!REVIEW_VERDICTS.contains(verdict)) {
            throw new BadRequestException("Verdict must be OK or CHANGES_REQUESTED.");
        }
        ObjectNode review = object(parse(pr.reviewJson));
        review.put("verdict", verdict);
        review.put("comment", input.comment() == null ? "" : input.comment());
        review.put("reviewer", input.reviewer() == null || input.reviewer().isBlank() ? "operator" : input.reviewer());
        review.put("reviewedAt", Instant.now().toString());
        pr.reviewJson = write(review);
        pr.reviewer = review.path("reviewer").asText("operator");
        pr.status = verdict.equals("OK") && checksOk(parse(pr.checksJson))
                ? PullRequestStatus.APPROVED : PullRequestStatus.CHANGES_REQUESTED;
        return view(pr);
    }

    @POST
    @Path("/{id}/merge")
    @Transactional
    public PullRequestView merge(@PathParam("id") String id, MergeInput input) {
        PullRequest pr = find(id);
        if (pr.status == PullRequestStatus.MERGED) {
            throw conflict("This pull request is already merged.");
        }
        if (pr.status == PullRequestStatus.CLOSED || pr.status == PullRequestStatus.BLOCKED) {
            throw conflict("This pull request is not mergeable.");
        }
        if (!checksOk(parse(pr.checksJson))) {
            throw conflict("Checks are not green.");
        }
        if (!reviewOk(parse(pr.reviewJson))) {
            throw conflict("The review is not OK.");
        }
        pr.status = PullRequestStatus.MERGED;
        pr.mergedAt = Instant.now();
        pr.mergedBy = input == null || input.mergedBy() == null || input.mergedBy().isBlank()
                ? "operator" : input.mergedBy();
        return view(pr);
    }

    @POST
    @Path("/{id}/close")
    @Transactional
    public PullRequestView close(@PathParam("id") String id) {
        PullRequest pr = find(id);
        if (pr.status == PullRequestStatus.MERGED) {
            throw conflict("A merged pull request cannot be closed.");
        }
        pr.status = PullRequestStatus.CLOSED;
        return view(pr);
    }

    private PullRequest find(String id) {
        PullRequest pr = PullRequest.findById(id);
        if (pr == null) {
            throw new NotFoundException("Unknown pull request " + id);
        }
        return pr;
    }

    private boolean checksOk(JsonNode checks) {
        JsonNode tests = checks.path("tests");
        int passed = tests.path("passed").asInt(-1);
        int total = tests.path("total").asInt(0);
        String gate = checks.path("gateDecision").asText("");
        boolean gateOk = checks.path("gateApproved").asBoolean(false)
                || gate.equals("allow") || gate.equals("approved");
        return total > 0 && passed == total && gateOk;
    }

    private boolean reviewOk(JsonNode review) {
        return review.path("verdict").asText("").equalsIgnoreCase("OK");
    }

    private PullRequestView view(PullRequest pr) {
        return new PullRequestView(
                pr.id, pr.ticketId, pr.missionId, pr.title, pr.risk, pr.status.name(),
                pr.diff, parse(pr.checksJson), parse(pr.reviewJson), pr.commitHash, pr.packetDigest,
                pr.branch, pr.base, pr.author, pr.reviewer, pr.createdAt, pr.mergedAt, pr.mergedBy);
    }

    private JsonNode parse(String text) {
        try {
            return text == null ? mapper.createObjectNode() : mapper.readTree(text);
        } catch (JsonProcessingException error) {
            return mapper.createObjectNode();
        }
    }

    private ObjectNode object(JsonNode node) {
        return node != null && node.isObject() ? (ObjectNode) node : mapper.createObjectNode();
    }

    private String write(JsonNode node) {
        try {
            return mapper.writeValueAsString(node);
        } catch (JsonProcessingException error) {
            throw new BadRequestException("Checks and review must be serializable JSON.");
        }
    }
}
