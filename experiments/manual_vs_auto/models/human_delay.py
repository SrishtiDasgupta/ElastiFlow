"""
Human delay model for manual intervention workflows.

Models the time engineers take to:
1. Notice that an iteration has completed
2. Analyze the results
3. Decide on parameters for the next iteration
4. Submit the next job

Uses a lognormal distribution with optional work-hour constraints.

Realistic variation is added through:
- Per-engineer work schedule jitter (different start/end times)
- Per-engineer response speed multiplier (some faster, some slower)
- Time-of-day efficiency variation (slower after lunch, etc.)
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass
class WorkHoursConfig:
    """Configuration for work hours constraints."""
    enabled: bool = True
    start_hour: int = 9    # 9 AM
    end_hour: int = 17     # 5 PM
    # Jitter settings for realistic variation
    start_jitter_hours: float = 1.5  # Engineers may start 1.5h earlier or later
    end_jitter_hours: float = 2.0    # Engineers may end 2h earlier or later

    def __post_init__(self):
        if self.start_hour < 0 or self.start_hour > 23:
            raise ValueError(f"start_hour must be 0-23, got {self.start_hour}")
        if self.end_hour < 0 or self.end_hour > 23:
            raise ValueError(f"end_hour must be 0-23, got {self.end_hour}")
        if self.enabled and self.end_hour <= self.start_hour:
            raise ValueError(f"end_hour ({self.end_hour}) must be > start_hour ({self.start_hour})")


@dataclass
class HumanDelayConfig:
    """Configuration for human delay model."""
    distribution: str = "lognormal"
    median_hours: float = 3.0
    sigma: float = 0.9
    min_hours: float = 0.5
    max_hours: float = 24.0
    work_hours: Optional[WorkHoursConfig] = None
    # Per-engineer variation settings
    engineer_speed_sigma: float = 0.3  # Log-normal sigma for per-engineer speed multiplier
    enable_time_of_day_variation: bool = True  # Slower after lunch, etc.

    def __post_init__(self):
        if self.median_hours <= 0:
            raise ValueError(f"median_hours must be > 0, got {self.median_hours}")
        if self.sigma <= 0:
            raise ValueError(f"sigma must be > 0, got {self.sigma}")
        if self.min_hours < 0:
            raise ValueError(f"min_hours must be >= 0, got {self.min_hours}")
        if self.max_hours <= self.min_hours:
            raise ValueError(f"max_hours ({self.max_hours}) must be > min_hours ({self.min_hours})")


@dataclass
class EngineerProfile:
    """
    Per-engineer characteristics that create realistic variation.
    Each workflow is assumed to be managed by a different engineer.
    """
    workflow_id: str
    speed_multiplier: float  # < 1.0 = faster, > 1.0 = slower
    work_start_hour: float   # Actual start hour for this engineer
    work_end_hour: float     # Actual end hour for this engineer


class HumanDelayModel:
    """
    Models human intervention delays using lognormal distribution.

    The lognormal distribution is appropriate because:
    - Delays are always positive
    - Most delays cluster around a typical value (the median)
    - There's a long tail of occasional longer delays
    - This matches observed human behavior in HPC workflows

    Realistic variation is achieved through:
    - Per-engineer speed multipliers (some people work faster)
    - Per-engineer work schedules (different start/end times)
    - Time-of-day efficiency (post-lunch slump, etc.)
    """

    SECONDS_PER_HOUR = 3600.0

    def __init__(self, config: HumanDelayConfig, seed: int = 42):
        """
        Initialize the human delay model.

        Args:
            config: Delay configuration parameters
            seed: Random seed for reproducibility
        """
        self.config = config
        self.rng = np.random.default_rng(seed)

        # Convert median to lognormal mu parameter
        # For lognormal: median = exp(mu), so mu = ln(median)
        self.mu = np.log(config.median_hours * self.SECONDS_PER_HOUR)
        self.sigma = config.sigma

        # Min/max in seconds
        self.min_delay = config.min_hours * self.SECONDS_PER_HOUR
        self.max_delay = config.max_hours * self.SECONDS_PER_HOUR

        # Per-engineer profiles (created on demand)
        self.engineer_profiles: Dict[str, EngineerProfile] = {}

    def _get_or_create_engineer_profile(self, workflow_id: str) -> EngineerProfile:
        """
        Get or create an engineer profile for a workflow.

        Each workflow is assumed to be managed by a unique engineer with
        their own work habits and schedule.

        Args:
            workflow_id: The workflow identifier

        Returns:
            EngineerProfile for this workflow's engineer
        """
        if workflow_id not in self.engineer_profiles:
            wh = self.config.work_hours

            # Generate per-engineer speed multiplier (lognormal, median=1.0)
            # This means some engineers are faster, some slower
            speed_multiplier = self.rng.lognormal(
                mean=0.0,  # ln(1.0) = 0, so median = 1.0
                sigma=self.config.engineer_speed_sigma
            )
            # Clip to reasonable range [0.5x to 2x speed]
            speed_multiplier = np.clip(speed_multiplier, 0.5, 2.0)

            # Generate per-engineer work schedule jitter
            if wh and wh.enabled:
                start_jitter = self.rng.uniform(
                    -wh.start_jitter_hours,
                    wh.start_jitter_hours
                )
                end_jitter = self.rng.uniform(
                    -wh.end_jitter_hours,
                    wh.end_jitter_hours
                )
                work_start = max(6.0, wh.start_hour + start_jitter)  # No earlier than 6 AM
                work_end = min(22.0, wh.end_hour + end_jitter)       # No later than 10 PM
                # Ensure at least 4 hours of work day
                if work_end - work_start < 4.0:
                    work_end = work_start + 4.0
            else:
                work_start = 0.0
                work_end = 24.0

            self.engineer_profiles[workflow_id] = EngineerProfile(
                workflow_id=workflow_id,
                speed_multiplier=speed_multiplier,
                work_start_hour=work_start,
                work_end_hour=work_end
            )

        return self.engineer_profiles[workflow_id]

    def _get_time_of_day_multiplier(self, hour_of_day: float) -> float:
        """
        Get efficiency multiplier based on time of day.

        Models the fact that people work at different speeds throughout the day:
        - Morning (9-12): Normal efficiency
        - Post-lunch (12-14): Slower (post-lunch slump)
        - Afternoon (14-17): Slightly slower
        - Evening: Slower (tired)

        Args:
            hour_of_day: Hour of day (0-24)

        Returns:
            Multiplier (> 1.0 means slower/longer delays)
        """
        if not self.config.enable_time_of_day_variation:
            return 1.0

        hour = hour_of_day % 24

        if 9 <= hour < 12:
            # Morning: peak efficiency
            return 0.9
        elif 12 <= hour < 14:
            # Post-lunch slump
            return 1.3
        elif 14 <= hour < 17:
            # Afternoon: slightly slower
            return 1.1
        elif 17 <= hour < 20:
            # Evening: tired
            return 1.4
        else:
            # Outside normal hours: slower
            return 1.5

    def sample_base_delay(self, workflow_id: str = None) -> float:
        """
        Sample a base delay from the lognormal distribution.

        Args:
            workflow_id: Optional workflow ID for per-engineer variation

        Returns:
            Delay in seconds, clipped to [min_delay, max_delay]
        """
        delay = self.rng.lognormal(mean=self.mu, sigma=self.sigma)

        # Apply per-engineer speed multiplier if workflow_id provided
        if workflow_id:
            profile = self._get_or_create_engineer_profile(workflow_id)
            delay *= profile.speed_multiplier

        return np.clip(delay, self.min_delay, self.max_delay)

    def expand_to_work_hours(
        self,
        iteration_end_time: float,
        base_delay: float,
        workflow_id: str = None
    ) -> float:
        """
        Expand delay to account for work hours if configured.

        If an iteration ends at 4 PM and the base delay is 3 hours,
        the actual delay should span overnight to the next workday.

        Uses per-engineer work schedules when workflow_id is provided.

        Args:
            iteration_end_time: Simulation time when iteration completed (seconds from sim start)
            base_delay: Base delay in seconds
            workflow_id: Optional workflow ID for per-engineer work schedule

        Returns:
            Adjusted delay accounting for work hours
        """
        if self.config.work_hours is None or not self.config.work_hours.enabled:
            return base_delay

        # Get per-engineer work schedule or use defaults
        if workflow_id:
            profile = self._get_or_create_engineer_profile(workflow_id)
            work_start = profile.work_start_hour
            work_end = profile.work_end_hour
        else:
            wh = self.config.work_hours
            work_start = wh.start_hour
            work_end = wh.end_hour

        # Convert iteration end time to hour of day (simplified: assume sim starts at midnight)
        end_hour = (iteration_end_time / self.SECONDS_PER_HOUR) % 24

        # Apply time-of-day efficiency multiplier
        time_multiplier = self._get_time_of_day_multiplier(end_hour)
        adjusted_base_delay = base_delay * time_multiplier

        # Calculate when the engineer would start working on the next submission
        # They need 'adjusted_base_delay' hours of active work time
        active_work_needed = adjusted_base_delay / self.SECONDS_PER_HOUR  # in hours

        actual_delay_hours = 0.0
        current_hour = end_hour

        while active_work_needed > 0:
            if current_hour < work_start:
                # Before work hours: wait until start
                actual_delay_hours += (work_start - current_hour)
                current_hour = work_start

            elif current_hour >= work_end:
                # After work hours: wait until next day start
                actual_delay_hours += (24 - current_hour + work_start)
                current_hour = work_start

            else:
                # During work hours: consume work time
                available_hours = work_end - current_hour
                work_done = min(available_hours, active_work_needed)
                actual_delay_hours += work_done
                active_work_needed -= work_done
                current_hour += work_done

                # If we hit end of day, move to next day
                if current_hour >= work_end and active_work_needed > 0:
                    actual_delay_hours += (24 - current_hour + work_start)
                    current_hour = work_start

        return actual_delay_hours * self.SECONDS_PER_HOUR

    def sample_delay(self, iteration_end_time: float = 0.0, workflow_id: str = None) -> float:
        """
        Sample a human delay accounting for work hours and per-engineer variation.

        This is the main method to call for getting intervention delays.

        Args:
            iteration_end_time: Simulation time when iteration completed
            workflow_id: Optional workflow ID for per-engineer variation

        Returns:
            Total delay in seconds before next iteration submission
        """
        base_delay = self.sample_base_delay(workflow_id)
        return self.expand_to_work_hours(iteration_end_time, base_delay, workflow_id)

    def get_statistics(self, num_samples: int = 10000) -> dict:
        """
        Generate statistics about the delay distribution.

        Useful for validation and debugging.

        Args:
            num_samples: Number of samples to generate

        Returns:
            Dictionary with mean, median, std, min, max in hours
        """
        samples = [self.sample_base_delay() for _ in range(num_samples)]
        samples_hours = [s / self.SECONDS_PER_HOUR for s in samples]

        return {
            'mean_hours': np.mean(samples_hours),
            'median_hours': np.median(samples_hours),
            'std_hours': np.std(samples_hours),
            'min_hours': np.min(samples_hours),
            'max_hours': np.max(samples_hours),
            'p25_hours': np.percentile(samples_hours, 25),
            'p75_hours': np.percentile(samples_hours, 75),
            'p95_hours': np.percentile(samples_hours, 95),
        }


def create_delay_model_from_config(config: dict, seed: int = 42) -> HumanDelayModel:
    """
    Create a HumanDelayModel from experiment configuration.

    Args:
        config: Experiment configuration dictionary
        seed: Random seed

    Returns:
        Configured HumanDelayModel instance
    """
    delay_config = config.get('human_delay', {})
    work_hours_config = delay_config.get('work_hours', {})

    wh = WorkHoursConfig(
        enabled=work_hours_config.get('enabled', True),
        start_hour=work_hours_config.get('start', 9),
        end_hour=work_hours_config.get('end', 17)
    ) if work_hours_config else None

    hd_config = HumanDelayConfig(
        distribution=delay_config.get('distribution', 'lognormal'),
        median_hours=delay_config.get('median_hours', 3.0),
        sigma=delay_config.get('sigma', 0.9),
        min_hours=delay_config.get('min_hours', 0.5),
        max_hours=delay_config.get('max_hours', 24.0),
        work_hours=wh
    )

    return HumanDelayModel(hd_config, seed=seed)


if __name__ == "__main__":
    # Test human delay model
    print("Human Delay Model Test")
    print("=" * 60)

    # Create model with default config
    config = HumanDelayConfig(
        median_hours=3.0,
        sigma=0.9,
        min_hours=0.5,
        max_hours=24.0,
        work_hours=WorkHoursConfig(enabled=True, start_hour=9, end_hour=17),
        engineer_speed_sigma=0.3,
        enable_time_of_day_variation=True
    )

    model = HumanDelayModel(config, seed=42)

    print("\nConfiguration:")
    print(f"  Median: {config.median_hours} hours")
    print(f"  Sigma: {config.sigma}")
    print(f"  Min: {config.min_hours} hours")
    print(f"  Max: {config.max_hours} hours")
    print(f"  Work hours: {config.work_hours.start_hour}:00 - {config.work_hours.end_hour}:00")
    print(f"  Work hour jitter: start ±{config.work_hours.start_jitter_hours}h, end ±{config.work_hours.end_jitter_hours}h")
    print(f"  Engineer speed sigma: {config.engineer_speed_sigma}")
    print(f"  Time-of-day variation: {config.enable_time_of_day_variation}")

    print("\nDistribution Statistics (10k samples, no work-hour adjustment):")
    stats = model.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value:.2f}")

    print("\nSample delays (without per-engineer variation):")
    for i in range(5):
        base = model.sample_base_delay()
        print(f"  Sample {i+1}: {base/3600:.2f} hours")

    print("\nPer-engineer profiles (5 different workflows):")
    for i in range(5):
        wf_id = f"wf-{i:04d}"
        profile = model._get_or_create_engineer_profile(wf_id)
        print(f"  {wf_id}: speed={profile.speed_multiplier:.2f}x, "
              f"work {profile.work_start_hour:.1f}:00 - {profile.work_end_hour:.1f}:00")

    print("\nSample delays WITH per-engineer variation:")
    for i in range(5):
        wf_id = f"wf-{i:04d}"
        delay = model.sample_delay(iteration_end_time=10*3600, workflow_id=wf_id)
        print(f"  {wf_id}: {delay/3600:.2f} hours (ending at 10:00)")

    print("\nTime-of-day multipliers:")
    for hour in [9, 11, 13, 15, 17, 19, 22]:
        mult = model._get_time_of_day_multiplier(hour)
        print(f"  {hour}:00 -> {mult:.2f}x")

    print("\nWork-hour expansion examples (with per-engineer schedules):")
    base_delay = 3 * 3600  # 3 hours
    wf_id = "wf-0001"
    profile = model._get_or_create_engineer_profile(wf_id)
    print(f"  Engineer {wf_id}: works {profile.work_start_hour:.1f}:00 - {profile.work_end_hour:.1f}:00")
    for end_hour in [10, 14, 16, 18, 22]:
        end_time = end_hour * 3600
        expanded = model.expand_to_work_hours(end_time, base_delay, wf_id)
        print(f"    End at {end_hour}:00, 3h work needed -> {expanded/3600:.1f}h actual delay")
