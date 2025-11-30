"""
Human delay model for manual intervention workflows.

Models the time engineers take to:
1. Notice that an iteration has completed
2. Analyze the results
3. Decide on parameters for the next iteration
4. Submit the next job

Uses a lognormal distribution with optional work-hour constraints.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class WorkHoursConfig:
    """Configuration for work hours constraints."""
    enabled: bool = True
    start_hour: int = 9    # 9 AM
    end_hour: int = 17     # 5 PM

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

    def __post_init__(self):
        if self.median_hours <= 0:
            raise ValueError(f"median_hours must be > 0, got {self.median_hours}")
        if self.sigma <= 0:
            raise ValueError(f"sigma must be > 0, got {self.sigma}")
        if self.min_hours < 0:
            raise ValueError(f"min_hours must be >= 0, got {self.min_hours}")
        if self.max_hours <= self.min_hours:
            raise ValueError(f"max_hours ({self.max_hours}) must be > min_hours ({self.min_hours})")


class HumanDelayModel:
    """
    Models human intervention delays using lognormal distribution.

    The lognormal distribution is appropriate because:
    - Delays are always positive
    - Most delays cluster around a typical value (the median)
    - There's a long tail of occasional longer delays
    - This matches observed human behavior in HPC workflows
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

    def sample_base_delay(self) -> float:
        """
        Sample a base delay from the lognormal distribution.

        Returns:
            Delay in seconds, clipped to [min_delay, max_delay]
        """
        delay = self.rng.lognormal(mean=self.mu, sigma=self.sigma)
        return np.clip(delay, self.min_delay, self.max_delay)

    def expand_to_work_hours(self, iteration_end_time: float, base_delay: float) -> float:
        """
        Expand delay to account for work hours if configured.

        If an iteration ends at 4 PM and the base delay is 3 hours,
        the actual delay should span overnight to the next workday.

        Args:
            iteration_end_time: Simulation time when iteration completed (seconds from sim start)
            base_delay: Base delay in seconds

        Returns:
            Adjusted delay accounting for work hours
        """
        if self.config.work_hours is None or not self.config.work_hours.enabled:
            return base_delay

        wh = self.config.work_hours

        # Convert iteration end time to hour of day (simplified: assume sim starts at midnight)
        # In a real simulation, you'd track actual calendar time
        end_hour = (iteration_end_time / self.SECONDS_PER_HOUR) % 24

        # Calculate when the engineer would start working on the next submission
        # They need 'base_delay' hours of active work time

        work_hours_per_day = wh.end_hour - wh.start_hour
        active_work_needed = base_delay / self.SECONDS_PER_HOUR  # in hours

        actual_delay_hours = 0.0
        current_hour = end_hour

        while active_work_needed > 0:
            if current_hour < wh.start_hour:
                # Before work hours: wait until start
                actual_delay_hours += (wh.start_hour - current_hour)
                current_hour = wh.start_hour

            elif current_hour >= wh.end_hour:
                # After work hours: wait until next day start
                actual_delay_hours += (24 - current_hour + wh.start_hour)
                current_hour = wh.start_hour

            else:
                # During work hours: consume work time
                available_hours = wh.end_hour - current_hour
                work_done = min(available_hours, active_work_needed)
                actual_delay_hours += work_done
                active_work_needed -= work_done
                current_hour += work_done

                # If we hit end of day, move to next day
                if current_hour >= wh.end_hour and active_work_needed > 0:
                    actual_delay_hours += (24 - current_hour + wh.start_hour)
                    current_hour = wh.start_hour

        return actual_delay_hours * self.SECONDS_PER_HOUR

    def sample_delay(self, iteration_end_time: float = 0.0) -> float:
        """
        Sample a human delay accounting for work hours.

        This is the main method to call for getting intervention delays.

        Args:
            iteration_end_time: Simulation time when iteration completed

        Returns:
            Total delay in seconds before next iteration submission
        """
        base_delay = self.sample_base_delay()
        return self.expand_to_work_hours(iteration_end_time, base_delay)

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
        work_hours=WorkHoursConfig(enabled=True, start_hour=9, end_hour=17)
    )

    model = HumanDelayModel(config, seed=42)

    print("\nConfiguration:")
    print(f"  Median: {config.median_hours} hours")
    print(f"  Sigma: {config.sigma}")
    print(f"  Min: {config.min_hours} hours")
    print(f"  Max: {config.max_hours} hours")
    print(f"  Work hours: {config.work_hours.start_hour}:00 - {config.work_hours.end_hour}:00")

    print("\nDistribution Statistics (10k samples, no work-hour adjustment):")
    stats = model.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value:.2f}")

    print("\nSample delays:")
    for i in range(5):
        base = model.sample_base_delay()
        print(f"  Sample {i+1}: {base/3600:.2f} hours")

    print("\nWork-hour expansion examples:")
    # Iteration ends at 4 PM (16:00), 3 hour delay
    base_delay = 3 * 3600  # 3 hours
    for end_hour in [10, 14, 16, 18, 22]:  # Different times of day
        end_time = end_hour * 3600  # Simulation seconds
        expanded = model.expand_to_work_hours(end_time, base_delay)
        print(f"  End at {end_hour}:00, 3h work needed -> {expanded/3600:.1f}h actual delay")
