args <- commandArgs(trailingOnly = TRUE)
output <- if (length(args)) args[[1]] else "docs_src/forest-examples.json"
inject_failure <- "--inject-failure" %in% args

if (!requireNamespace("jsonlite", quietly = TRUE) || !requireNamespace("metafor", quietly = TRUE)) {
  stop("jsonlite and metafor are required", call. = FALSE)
}

scenarios <- list(
  list(id = "consistent", label = "Consistent effects", description = "All synthetic study estimates point upward with similar uncertainty.", effects = c(1.15, 0.92, 1.08, 0.86), se = c(0.22, 0.25, 0.20, 0.24)),
  list(id = "uncertain", label = "One uncertain study", description = "Three estimates are precise; one has a wide interval and therefore little weight.", effects = c(1.10, 0.96, 1.22, 0.30), se = c(0.24, 0.26, 0.23, 0.95)),
  list(id = "opposing", label = "An opposing study", description = "One study estimates an effect in the other direction; the pooled estimate does not establish agreement.", effects = c(1.20, 1.05, -0.85, 1.10), se = c(0.24, 0.22, 0.28, 0.25))
)
studies <- paste("Study", LETTERS[1:4])

manual_dl <- function(yi, sei) {
  vi <- pmax(sei, 1e-6)^2
  fixed_weights <- 1 / vi
  fixed_mean <- sum(fixed_weights * yi) / sum(fixed_weights)
  q <- sum(fixed_weights * (yi - fixed_mean)^2)
  c_term <- sum(fixed_weights) - sum(fixed_weights^2) / sum(fixed_weights)
  tau2 <- max(0, (q - (length(yi) - 1)) / c_term)
  weights <- 1 / (vi + tau2)
  estimate <- sum(weights * yi) / sum(weights)
  se <- sqrt(1 / sum(weights))
  list(estimate = estimate, ci_low = estimate - stats::qnorm(.975) * se,
       ci_high = estimate + stats::qnorm(.975) * se, tau2 = tau2,
       weights = weights / sum(weights))
}

fit_scenario <- function(scenario, omitted = NA_integer_) {
  keep <- seq_along(scenario$effects)
  if (!is.na(omitted)) keep <- setdiff(keep, omitted)
  yi <- scenario$effects[keep]
  sei <- scenario$se[keep]
  fit <- metafor::rma.uni(yi = yi, sei = pmax(sei, 1e-6), method = "DL", test = "z")
  check <- manual_dl(yi, sei)
  if (!isTRUE(all.equal(as.numeric(fit$b), check$estimate, tolerance = 1e-10)) ||
      !isTRUE(all.equal(fit$ci.lb, check$ci_low, tolerance = 1e-10)) ||
      !isTRUE(all.equal(fit$ci.ub, check$ci_high, tolerance = 1e-10)) ||
      !isTRUE(all.equal(fit$tau2, check$tau2, tolerance = 1e-10))) {
    stop("manual DerSimonian-Laird calculation disagrees with metafor", call. = FALSE)
  }
  weights <- numeric(4)
  weights[keep] <- check$weights
  list(
    omitted = if (is.na(omitted)) NULL else omitted,
    estimate = as.numeric(fit$b), ciLow = fit$ci.lb, ciHigh = fit$ci.ub,
    tau2 = fit$tau2, weights = weights
  )
}

payload <- list(
  provenance = list(
    source = "Synthetic illustrative unshrunken log2 fold-change estimates and standard errors.",
    method = "DerSimonian-Laird random-effects pooling with z-Wald 95% confidence intervals.",
    limitations = "This explorer shows pooled effects only. It does not calculate combined p-values, adjusted p-values, meta_sig, or biological replication.",
    r_version = R.version.string,
    metafor_version = as.character(utils::packageVersion("metafor"))
  ),
  studies = studies,
  scenarios = lapply(scenarios, function(scenario) {
    scenario$fits <- c(list(fit_scenario(scenario)), lapply(seq_along(studies), function(index) fit_scenario(scenario, index)))
    scenario
  })
)

validate_payload <- function(x) {
  stopifnot(length(x$studies) == 4L, length(x$scenarios) == 3L)
  for (scenario in x$scenarios) {
    stopifnot(length(scenario$effects) == 4L, length(scenario$se) == 4L, all(scenario$se > 0), length(scenario$fits) == 5L)
    for (fit in scenario$fits) {
      omitted <- if (is.null(fit$omitted)) NA_integer_ else as.integer(fit$omitted)
      expected <- fit_scenario(scenario, omitted)
      stopifnot(isTRUE(all.equal(fit$estimate, expected$estimate, tolerance = 1e-10)),
                isTRUE(all.equal(fit$ciLow, expected$ciLow, tolerance = 1e-10)),
                isTRUE(all.equal(fit$ciHigh, expected$ciHigh, tolerance = 1e-10)),
                isTRUE(all.equal(fit$tau2, expected$tau2, tolerance = 1e-10)),
                isTRUE(all.equal(fit$weights, expected$weights, tolerance = 1e-10)))
    }
  }
}

if (inject_failure) payload$scenarios[[1]]$fits[[1]]$estimate <- payload$scenarios[[1]]$fits[[1]]$estimate + 0.1
result <- tryCatch({ validate_payload(payload); TRUE }, error = function(e) { message("FOREST_FIXTURE_VALIDATION_FAILED: ", conditionMessage(e)); FALSE })
if (!result) quit(status = 1L)

jsonlite::write_json(payload, output, auto_unbox = TRUE, null = "null", digits = 12, pretty = TRUE)
cat("FOREST_FIXTURE_PASS\n")
print(sessionInfo())
