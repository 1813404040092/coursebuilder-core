# Copyright 2013 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Implementation of the review subsystem."""

__author__ = [
    'johncox@google.com (John Cox)',
]

import datetime

from models import counters
from models import review
from models import utils
from modules.review import peer
from google.appengine.ext import db


# In-process increment-only performance counters.
COUNTER_ADD_REVIEWER_BAD_SUMMARY_KEY = counters.PerfCounter(
    'gcb-add-reviewer-bad-summary-key',
    'number of times add_reviewer() failed due to a bad review summary key')
COUNTER_ADD_REVIEWER_SET_ASSIGNER_KIND_HUMAN = counters.PerfCounter(
    'gcb-add-reviewer-set-assigner-kind-human',
    ("number of times add_reviewer() changed an existing step's assigner_kind "
     'to ASSIGNER_KIND_HUMAN'))
COUNTER_ADD_REVIEWER_CREATE_REVIEW_STEP = counters.PerfCounter(
    'gcb-add-reviewer-create-review-step',
    'number of times add_reviewer() created a new review step')
COUNTER_ADD_REVIEWER_EXPIRED_STEP_REASSIGNED = counters.PerfCounter(
    'gcb-add-reviewer-expired-step-reassigned',
    'number of times add_reviewer() reassigned an expired step')
COUNTER_ADD_REVIEWER_FAILED = counters.PerfCounter(
    'gcb-add-reviewer-failed',
    'number of times add_reviewer() had a fatal error')
COUNTER_ADD_REVIEWER_REMOVED_STEP_UNREMOVED = counters.PerfCounter(
    'gcb-add-reviewer-removed-step-unremoved',
    'number of times add_reviewer() unremoved a removed review step')
COUNTER_ADD_REVIEWER_START = counters.PerfCounter(
    'gcb-add-reviewer-start',
    'number of times add_reviewer() has started processing')
COUNTER_ADD_REVIEWER_SUCCESS = counters.PerfCounter(
    'gcb-add-reviewer-success',
    'number of times add_reviewer() completed successfully')
COUNTER_ADD_REVIEWER_UNREMOVED_STEP_FAILED = counters.PerfCounter(
    'gcb-add-reviewer-unremoved-step-failed',
    ('number of times add_reviewer() failed on an unremoved step with a fatal '
     'error'))

COUNTER_DELETE_REVIEWER_ALREADY_REMOVED = counters.PerfCounter(
    'gcb-review-delete-reviewer-already-removed',
    ('number of times delete_reviewer() called on review step with removed '
     'already True'))
COUNTER_DELETE_REVIEWER_FAILED = counters.PerfCounter(
    'gcb-review-delete-reviewer-failed',
    'number of times delete_reviewer() had a fatal error')
COUNTER_DELETE_REVIEWER_START = counters.PerfCounter(
    'gcb-review-delete-reviewer-start',
    'number of times delete_reviewer() has started processing')
COUNTER_DELETE_REVIEWER_STEP_MISS = counters.PerfCounter(
    'gcb-review-delete-reviewer-step-miss',
    'number of times delete_reviewer() found a missing review step')
COUNTER_DELETE_REVIEWER_SUCCESS = counters.PerfCounter(
    'gcb-review-delete-reviewer-success',
    'number of times delete_reviewer() completed successfully')
COUNTER_DELETE_REVIEWER_SUMMARY_MISS = counters.PerfCounter(
    'gcb-review-delete-reviewer-summary-miss',
    'number of times delete_reviewer() found a missing review summary')

COUNTER_EXPIRE_REVIEW_CANNOT_TRANSITION = counters.PerfCounter(
    'gcb-expire-review-cannot-transition',
    ('number of times expire_review() was called on a review step that could '
     'not be transitioned to REVIEW_STATE_EXPIRED'))
COUNTER_EXPIRE_REVIEW_FAILED = counters.PerfCounter(
    'gcb-expire-review-failed',
    'number of times expire_review() had a fatal error')
COUNTER_EXPIRE_REVIEW_START = counters.PerfCounter(
    'gcb-expire-review-start',
    'number of times expire_review() has started processing')
COUNTER_EXPIRE_REVIEW_STEP_MISS = counters.PerfCounter(
    'gcb-expire-review-step-miss',
    'number of times expire_review() found a missing review step')
COUNTER_EXPIRE_REVIEW_SUCCESS = counters.PerfCounter(
    'gcb-expire-review-success',
    'number of times expire_review() completed successfully')
COUNTER_EXPIRE_REVIEW_SUMMARY_MISS = counters.PerfCounter(
    'gcb-expire-review-summary-miss',
    'number of times expire_review() found a missing review summary')

COUNTER_EXPIRE_OLD_REVIEWS_FOR_UNIT_EXPIRE = counters.PerfCounter(
    'gcb-expire-old-reviews-for-unit-expire',
    'number of records expire_old_reviews_for_unit() has expired')
COUNTER_EXPIRE_OLD_REVIEWS_FOR_UNIT_SKIP = counters.PerfCounter(
    'gcb-expire-old-reviews-for-unit-skip',
    ('number of times expire_old_reviews_for_unit() skipped a record due to an '
     'error'))
COUNTER_EXPIRE_OLD_REVIEWS_FOR_UNIT_START = counters.PerfCounter(
    'gcb-expire-old-reviews-for-unit-start',
    'number of times expire_old_reviews_for_unit() has started processing')
COUNTER_EXPIRE_OLD_REVIEWS_FOR_UNIT_SUCCESS = counters.PerfCounter(
    'gcb-expire-old-reviews-for-unit-success',
    'number of times expire_old_reviews_for_unit() completed successfully')

COUNTER_START_REVIEW_PROCESS_FOR_ALREADY_STARTED = counters.PerfCounter(
    'gcb-start-review-process-for-already-started',
    ('number of times start_review_process_for() called when review already '
     'started'))
COUNTER_START_REVIEW_PROCESS_FOR_FAILED = counters.PerfCounter(
    'gcb-start-review-process-for-failed',
    'number of times start_review_process_for() had a fatal error')
COUNTER_START_REVIEW_PROCESS_FOR_START = counters.PerfCounter(
    'gcb-start-review-process-for-start',
    'number of times start_review_process_for() has started processing')
COUNTER_START_REVIEW_PROCESS_FOR_SUCCESS = counters.PerfCounter(
    'gcb-start-review-process-for-success',
    'number of times start_review_process_for() completed successfully')


class Error(Exception):
    """Base error class."""


class RemovedError(Error):
    """Raised when an op cannot be performed on a step because it is removed."""

    def __init__(self, message, value):
        """Constructs a new RemovedError."""
        super(RemovedError, self).__init__(message)
        self.value = value

    def __str__(self):
        return '%s: removed is %s' % (self.message, self.value)


class ReviewProcessAlreadyStartedError(Error):
    """Raised when someone attempts to start a review process in progress."""


class TransitionError(Error):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, message, before, after):
        """Constructs a new TransitionError.

        Args:
            message: string. Exception message.
            before: string in peer.ReviewStates (though this is unenforced).
                State we attempted to transition from.
            after: string in peer.ReviewStates (though this is unenforced).
                State we attempted to transition to.
        """
        super(TransitionError, self).__init__(message)
        self.after = after
        self.before = before

    def __str__(self):
        return '%s: attempted to transition from %s to %s' % (
            self.message, self.before, self.after)


class _DomainObject(object):
    """Domain object for review-related classes."""

    # db.Model. The model definition associated with this domain object class.
    _model = None

    @classmethod
    def make_key(cls, id_or_name, namespace):
        """Makes a db.Key for a domain object."""
        assert cls._model is not None
        return db.Key.from_path(
            cls._model.kind(), id_or_name, namespace=namespace)


class Review(_DomainObject):
    """Domain object for a student work submission."""

    _model = review.Review

    def __init__(self, contents=None, key=None):
        self._contents = contents
        self._key = key

    @property
    def contents(self):
        return self._contents

    @property
    def key(self):
        return self._key


class ReviewStep(_DomainObject):
    """Domain object for the status of a single review at a point in time."""

    _model = peer.ReviewStep

    def __init__(
        self, assigner_kind=None, change_date=None, create_date=None, key=None,
        removed=None, review_key=None, review_summary_key=None,
        reviewee_key=None, reviewer_key=None, state=None, submission_key=None,
        unit_id=None):
        self._assigner_kind = assigner_kind
        self._change_date = change_date
        self._create_date = create_date
        self._key = key
        self._removed = removed
        self._review_key = review_key
        self._review_summary_key = review_summary_key
        self._reviewee_key = reviewee_key
        self._reviewer_key = reviewer_key
        self._state = state
        self._submission_key = submission_key
        self._unit_id = unit_id

    @property
    def assigner_kind(self):
        return self._assigner_kind

    @property
    def change_date(self):
        return self._change_date

    @property
    def create_date(self):
        return self._create_date

    @property
    def key(self):
        return self._key

    @property
    def removed(self):
        return self._removed

    @property
    def review_key(self):
        return self._review_key

    @property
    def review_summary_key(self):
        return self._review_summary_key

    @property
    def reviewee_key(self):
        return self._reviewee_key

    @property
    def reviewer_key(self):
        return self._reviewer_key

    @property
    def state(self):
        return self._state

    @property
    def submission_key(self):
        return self._submission_key

    @property
    def unit_id(self):
        return self._unit_id


class ReviewSummary(_DomainObject):
    """Domain object for review state aggregate entities."""

    _model = peer.ReviewSummary

    def __init__(
        self, assigned_count=None, completed_count=None, change_date=None,
        create_date=None, key=None, reviewee_key=None, submission_key=None,
        unit_id=None):
        self._assigned_count = assigned_count
        self._completed_count = completed_count
        self._change_date = change_date
        self._create_date = create_date
        self._key = key
        self._reviewee_key = reviewee_key
        self._submission_key = submission_key
        self._unit_id = unit_id

    @property
    def assigned_count(self):
        return self._assigned_count

    @property
    def completed_count(self):
        return self._completed_count

    @property
    def change_date(self):
        return self._change_date

    @property
    def create_date(self):
        return self._create_date

    @property
    def key(self):
        return self._key

    @property
    def reviewee_key(self):
        return self._reviewee_key

    @property
    def submission_key(self):
        return self._submission_key

    @property
    def unit_id(self):
        return self._unit_id


class Submission(_DomainObject):
    """Domain object for a student work submission."""

    _model = review.Submission

    def __init__(self, contents=None, key=None):
        self._contents = contents
        self._key = key

    @property
    def contents(self):
        return self._contents

    @property
    def key(self):
        return self._key


class Manager(object):
    """Object that manages the review subsystem."""

    @classmethod
    def add_reviewer(cls, unit_id, submission_key, reviewee_key, reviewer_key):
        """Adds a reviewer for a submission.

        If there is no pre-existing review step, one will be created.

        Attempting to add an existing unremoved step in REVIEW_STATE_ASSIGNED or
        REVIEW_STATE_COMPLETED is an error.

        If there is an existing unremoved review in REVIEW_STATE_EXPIRED, it
        will be put in REVIEW_STATE_ASSIGNED. If there is a removed review in
        REVIEW_STATE_ASSIGNED or REVIEW_STATE_EXPIRED, it will be put in
        REVIEW_STATE_ASSIGNED and unremoved. If it is in REVIEW_STATE_COMPLETED,
        it will be unremoved but its state will not change. In all these cases
        the assigner kind will be set to ASSIGNER_KIND_HUMAN.

        Args:
            unit_id: string. Unique identifier for a unit.
            submission_key: db.Key of models.review.Submission. The submission
                being registered.
            reviewee_key: db.Key of models.models.Student. The student who
                authored the submission.
            reviewer_key: db.Key of models.models.Student. The student to add as
                a reviewer.

        Raises:
            TransitionError: if there is a pre-existing review step found in
                REVIEW_STATE_ASSIGNED|COMPLETED.

        Returns:
            db.Key of written review step.
        """
        try:
            COUNTER_ADD_REVIEWER_START.inc()
            key = cls._add_reviewer(
                unit_id, submission_key, reviewee_key, reviewer_key)
            COUNTER_ADD_REVIEWER_SUCCESS.inc()
            return key
        except Exception as e:
            COUNTER_ADD_REVIEWER_FAILED.inc()
            raise e

    @classmethod
    @db.transactional(xg=True)
    def _add_reviewer(cls, unit_id, submission_key, reviewee_key, reviewer_key):
        found = peer.ReviewStep.get_by_key_name(
            peer.ReviewStep.key_name(
                unit_id, submission_key, reviewee_key, reviewer_key))
        if not found:
            return cls._add_new_reviewer(
                unit_id, submission_key, reviewee_key, reviewer_key)
        else:
            return cls._add_reviewer_update_step(found)

    @classmethod
    def _add_new_reviewer(
        cls, unit_id, submission_key, reviewee_key, reviewer_key):
        summary = peer.ReviewSummary(
            assigned_count=1, reviewee_key=reviewee_key,
            submission_key=submission_key, unit_id=unit_id)
        # Synthesize summary key to avoid a second synchronous put op.
        summary_key = db.Key.from_path(
            peer.ReviewSummary.kind(),
            peer.ReviewSummary.key_name(unit_id, submission_key, reviewee_key))
        step = peer.ReviewStep(
            assigner_kind=peer.ASSIGNER_KIND_HUMAN,
            review_summary_key=summary_key, reviewee_key=reviewee_key,
            reviewer_key=reviewer_key, state=peer.REVIEW_STATE_ASSIGNED,
            submission_key=submission_key, unit_id=unit_id)
        step_key, written_summary_key = db.put([step, summary])

        if summary_key != written_summary_key:
            COUNTER_ADD_REVIEWER_BAD_SUMMARY_KEY.inc()
            raise AssertionError(
                'Synthesized invalid review summary key %s' % repr(summary_key))

        COUNTER_ADD_REVIEWER_CREATE_REVIEW_STEP.inc()
        return step_key

    @classmethod
    def _add_reviewer_update_step(cls, step):
        should_increment_human = False
        should_increment_reassigned = False
        should_increment_unremoved = False
        summary = peer.ReviewSummary.get(step.review_summary_key)

        if not summary:
            COUNTER_ADD_REVIEWER_BAD_SUMMARY_KEY.inc()
            raise AssertionError(
                'Found invalid review summary key %s' % repr(
                    step.review_summary_key))

        if not step.removed:

            if step.state == peer.REVIEW_STATE_EXPIRED:
                should_increment_reassigned = True
                step.state = peer.REVIEW_STATE_ASSIGNED
                summary.decrement_count(peer.REVIEW_STATE_EXPIRED)
                summary.increment_count(peer.REVIEW_STATE_ASSIGNED)
            elif (step.state == peer.REVIEW_STATE_ASSIGNED or
                  step.state == peer.REVIEW_STATE_COMPLETED):
                COUNTER_ADD_REVIEWER_UNREMOVED_STEP_FAILED.inc()
                raise TransitionError(
                    'Unable to add new reviewer to step %s' % (
                        repr(step.key())),
                    step.state, peer.REVIEW_STATE_ASSIGNED)
        else:
            should_increment_unremoved = True
            step.removed = False

            if step.state != peer.REVIEW_STATE_EXPIRED:
                summary.increment_count(step.state)
            else:
                should_increment_reassigned = True
                step.state = peer.REVIEW_STATE_ASSIGNED
                summary.decrement_count(peer.REVIEW_STATE_EXPIRED)
                summary.increment_count(peer.REVIEW_STATE_ASSIGNED)

        if step.assigner_kind != peer.ASSIGNER_KIND_HUMAN:
            should_increment_human = True
            step.assigner_kind = peer.ASSIGNER_KIND_HUMAN

        step_key = db.put([step, summary])[0]

        if should_increment_human:
            COUNTER_ADD_REVIEWER_SET_ASSIGNER_KIND_HUMAN.inc()
        if should_increment_reassigned:
            COUNTER_ADD_REVIEWER_EXPIRED_STEP_REASSIGNED.inc()
        if should_increment_unremoved:
            COUNTER_ADD_REVIEWER_REMOVED_STEP_UNREMOVED.inc()

        return step_key

    @classmethod
    def delete_reviewer(cls, review_step_key):
        """Deletes the given review step.

        We do not physically delete the review step; we mark it as removed,
        meaning it will be ignored from most queries and the associated review
        summary will have its corresponding state count decremented. Calling
        this method on a removed review step is an error.

        Args:
            review_step_key: db.Key of models.review.ReviewStep. The review step
                to delete.

        Raises:
            KeyError: if there is no review step with the given key, or if the
                step references a review summary that does not exist.
            RemovedError: if called on a review step that has already been
                marked removed.

        Returns:
            db.Key of deleted review step.
        """
        try:
            COUNTER_DELETE_REVIEWER_START.inc()
            key = cls._mark_review_step_removed(review_step_key)
            COUNTER_DELETE_REVIEWER_SUCCESS.inc()
            return key
        except Exception as e:
            COUNTER_DELETE_REVIEWER_FAILED.inc()
            raise e

    @classmethod
    @db.transactional(xg=True)
    def _mark_review_step_removed(cls, review_step_key):
        step = db.get(review_step_key)
        if not step:
            COUNTER_DELETE_REVIEWER_STEP_MISS.inc()
            raise KeyError(
                'No review step found with key %s' % repr(review_step_key))
        if step.removed:
            COUNTER_DELETE_REVIEWER_ALREADY_REMOVED.inc()
            raise RemovedError(
                'Cannot remove step %s' % repr(review_step_key), step.removed)
        summary = db.get(step.review_summary_key)

        if not summary:
            COUNTER_DELETE_REVIEWER_SUMMARY_MISS.inc()
            raise KeyError(
                'No review summary found with key %s' % repr(
                    step.review_summary_key))

        step.removed = True
        summary.decrement_count(step.state)
        return db.put([step, summary])[0]

    @classmethod
    def expire_review(cls, review_step_key):
        """Puts a review step in state REVIEW_STATE_EXPIRED.

        Args:
            review_step_key: db.Key of models.review.ReviewStep. The review step
                to expire.

        Raises:
            KeyError: if there is no review with the given key, or the step
                references a review summary that does not exist.
            RemovedError: if called on a step that is removed.
            TransitionError: if called on a review step that cannot be
                transitioned to REVIEW_STATE_EXPIRED (that is, it is already in
                REVIEW_STATE_COMPLETED or REVIEW_STATE_EXPIRED).

        Returns:
            db.Key of the expired review step.
        """
        try:
            COUNTER_EXPIRE_REVIEW_START.inc()
            key = cls._transition_state_to_expired(review_step_key)
            COUNTER_EXPIRE_REVIEW_SUCCESS.inc()
            return key
        except Exception as e:
            COUNTER_EXPIRE_REVIEW_FAILED.inc()
            raise e

    @classmethod
    @db.transactional(xg=True)
    def _transition_state_to_expired(cls, review_step_key):
        step = db.get(review_step_key)

        if not step:
            COUNTER_EXPIRE_REVIEW_STEP_MISS.inc()
            raise KeyError(
                'No review step found with key %s' % repr(review_step_key))

        if step.removed:
            COUNTER_EXPIRE_REVIEW_CANNOT_TRANSITION.inc()
            raise RemovedError(
                'Cannot transition step %s' % repr(review_step_key),
                step.removed)

        if step.state in (
                peer.REVIEW_STATE_COMPLETED, peer.REVIEW_STATE_EXPIRED):
            COUNTER_EXPIRE_REVIEW_CANNOT_TRANSITION.inc()
            raise TransitionError(
                'Cannot transition step %s' % repr(review_step_key),
                step.state, peer.REVIEW_STATE_EXPIRED)

        summary = db.get(step.review_summary_key)

        if not summary:
            COUNTER_EXPIRE_REVIEW_SUMMARY_MISS.inc()
            raise KeyError(
                'No review summary found with key %s' % repr(
                    step.review_summary_key))

        summary.decrement_count(step.state)
        step.state = peer.REVIEW_STATE_EXPIRED
        summary.increment_count(step.state)
        return db.put([step, summary])[0]

    @classmethod
    def expire_old_reviews_for_unit(cls, review_window_mins, unit_id):
        """Finds and expires all old review steps for a single unit.

        Args:
            review_window_mins: int. Number of minutes before we expire reviews
                assigned by peer.ASSIGNER_KIND_AUTO.
            unit_id: string. Id of the unit to restrict the query to.

        Returns:
            2-tuple of list of db.Key of peer.ReviewStep. 0th element is keys
            that were written successfully; 1st element is keys that we failed
            to update.
        """
        query = cls.get_expiry_query(review_window_mins, unit_id)
        mapper = utils.QueryMapper(query, report_every=100)
        expired_keys = []
        exception_keys = []

        def map_fn(review_step_key, expired_keys, exception_keys):
            try:
                expired_keys.append(cls.expire_review(review_step_key))
            except:  # All errors are the same. pylint: disable-msg=bare-except
                # Skip. Either the entity was updated between the query and
                # the update, meaning we don't need to expire it; or we ran into
                # a transient datastore error, meaning we'll expire it next
                # time.
                COUNTER_EXPIRE_OLD_REVIEWS_FOR_UNIT_SKIP.inc()
                exception_keys.append(review_step_key)

        COUNTER_EXPIRE_OLD_REVIEWS_FOR_UNIT_START.inc()

        mapper.run(map_fn, expired_keys, exception_keys)
        COUNTER_EXPIRE_OLD_REVIEWS_FOR_UNIT_EXPIRE.inc(
            increment=len(expired_keys))
        COUNTER_EXPIRE_OLD_REVIEWS_FOR_UNIT_SUCCESS.inc()
        return expired_keys, exception_keys

    @classmethod
    def get_expiry_query(
        cls, review_window_mins, unit_id, now_fn=datetime.datetime.now):
        """Gets a db.Query that returns review steps to mark expired.

        Results are items that were assigned by machine, are currently assigned,
        are not removed, were last updated more than review_window_mins ago,
        and are ordered by change date ascending.

        Args:
            review_window_mins: int. Number of minutes before we expire reviews
                assigned by peer.ASSIGNER_KIND_AUTO.
            unit_id: string. Id of the unit to restrict the query to.
            now_fn: function that returns the current UTC datetime. Injectable
                for tests only.

        Returns:
            db.Query.
        """
        get_before = now_fn() - datetime.timedelta(
            minutes=review_window_mins)
        return peer.ReviewStep.all(keys_only=True).filter(
            peer.ReviewStep.unit_id.name, unit_id,
        ).filter(
            peer.ReviewStep.assigner_kind.name, peer.ASSIGNER_KIND_AUTO
        ).filter(
            peer.ReviewStep.state.name, peer.REVIEW_STATE_ASSIGNED
        ).filter(
            peer.ReviewStep.removed.name, False
        ).filter(
            '%s <=' % peer.ReviewStep.change_date.name, get_before
        ).order(
            peer.ReviewStep.change_date.name)

    @classmethod
    def start_review_process_for(cls, unit_id, submission_key, reviewee_key):
        """Registers a new submission with the review subsystem.

        Once registered, reviews can be assigned against a given submission,
        either by humans or by machine. No reviews are assigned during
        registration -- this method merely makes them assignable.

        Args:
            unit_id: string. Unique identifier for a unit.
            submission_key: db.Key of models.review.Submission. The submission
                being registered.
            reviewee_key: db.Key of models.models.Student. The student who
                authored the submission.

        Raises:
            ReviewProcessAlreadyStartedError: if the review process has already
                been started for this student's submission.
            db.BadValueError: if passed args are invalid.

        Returns:
            db.Key of created ReviewSummary.
        """
        try:
            COUNTER_START_REVIEW_PROCESS_FOR_START.inc()
            key = cls._create_review_summary(
                reviewee_key, submission_key, unit_id)
            COUNTER_START_REVIEW_PROCESS_FOR_SUCCESS.inc()
            return key
        except Exception as e:
            COUNTER_START_REVIEW_PROCESS_FOR_FAILED.inc()
            raise e

    @classmethod
    @db.transactional(xg=True)
    def _create_review_summary(cls, reviewee_key, submission_key, unit_id):
        collision = peer.ReviewSummary.get_by_key_name(
            peer.ReviewSummary.key_name(unit_id, submission_key, reviewee_key))

        if collision:
            COUNTER_START_REVIEW_PROCESS_FOR_ALREADY_STARTED.inc()
            raise ReviewProcessAlreadyStartedError()

        return peer.ReviewSummary(
            reviewee_key=reviewee_key, submission_key=submission_key,
            unit_id=unit_id,
        ).put()